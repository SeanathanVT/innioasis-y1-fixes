#!/usr/bin/env python3
"""
patch_libextavrcp_jni.py — Patch stock libextavrcp_jni.so -> libextavrcp_jni.so.patched

Stock binary md5:  fd2ce74db9389980b55bccf3d8f15660
Output md5:        6c348ed9b2da4bb9cc364c16d20e3527

--- Full verified call chain (Java -> daemon socket) ---

  Java BTAvrcpMusicAdapter.checkCapability()
    -> native activateConfig_3req(tg_feature_bits, ct_feature_bits, ...)
  [BluetoothAvrcpService_activateConfig_3req @ 0x375C, 120 bytes]:
    Feature bitmask decision tree:
      bits & 0x00104010 (bits 4,14,20) -> tg_feature_code = 0x0e (AVRCP 1.4)
      bits & 0x00082008 (bits 3,13,19) -> tg_feature_code = 0x0d (intermediate)
      else                             -> tg_feature_code = 0x0a (AVRCP 1.3)
    Stores tg_feature_code -> g_tg_feature global @ 0xD29C (verified)
    Stores ct_feature_code -> g_ct_feature global @ 0xD004 (verified)
    -> native activate_1req(conn_handle)
  [BluetoothAvrcpService_activate_1req @ 0x3E3C, 180 bytes]:
    Reads g_tg_feature (0xD29C) -> r3
    Reads g_ct_feature (0xD004) -> stack[0]
    -> btmtk_avrcp_send_activate_req(conn+8, 0, 0, tg_feature_code, ct_feature_code)
  [libextavrcp.so: btmtk_avrcp_send_activate_req @ 0x19CC]:
    8-byte socket payload: [0..3]=0x00000000, [4]=0, [5]=0, [6]=tg_code, [7]=ct_code
    -> AVRCP_SendMessage(conn, 0x1bfe, &payload, 8)
    -> send() -> abstract socket bt.ext.adp.avrcp (ANDROID_SOCKET_NAMESPACE_ABSTRACT)

  Global address derivation (activateConfig_3req writer):
    ADD r14, r15 at 0x37ae: 0x9aea + PC(0x37b2) = 0xD29C  (g_tg_feature)
    ADD r12, r15 at 0x37b0: 0x9850 + PC(0x37b4) = 0xD004  (g_ct_feature)
    STRB.W r4, [r14, #0] at 0x37b2 -> writes tg_feature_code to 0xD29C
    STRB.W r5, [r12, #0] at 0x37b6 -> writes ct_feature_code to 0xD004

--- Patches 1+2 (activateConfig_3req @ 0x375C) — defense-in-depth ---

  These bypass the bitmask decision tree, hardcoding the outputs regardless of
  what checkCapability() computed. They complement (do not replace) the ODEX
  patch: the ODEX patch enables the 1.4 capability block so the bitmask reaches
  activateConfig_3req with the right bits; these patches guarantee g_tg_feature=0x0e
  even if the bitmask produces a wrong result.

  Patch 1: sdpfeature = 0x23 (Cat1+Cat2+PlayerAppSettings)
    At 0x3764, r5 holds sdpfeature sourced from r3 (a capability register that
    may be zero before the 1.4 block initializes). mov r5,r3 -> movs r5,#0x23.

  Patch 2: g_tg_feature = 0x0e (AVRCP 1.4)
    At 0x37a8 the else-branch has set r4=0x0a (1.3). This instruction
    (movs r0,#1, unrelated setup) is repurposed to overwrite r4=0x0e before
    the STRB.W to g_tg_feature at 0x37b2.

--- Patches 3+4 (getCapabilitiesRspNative, FUN_005de8) ---

  NOTE: FUN_005de8 is getCapabilitiesRspNative, NOT the CONNECT_CNF handler.
  The actual CONNECT_CNF handler is at 0x62EA (TBH dispatch table at 0x60B8,
  msg_id=505, TBH index=4, entry=0x0117). The CONNECT_CNF handler reads and
  logs tg_feature but does not gate on it; it is unrelated to cardinality.

  getCapabilitiesRspNative runs when the car CT sends a GetCapabilities(EventList)
  request (CapabilityId=2). Stock code caps the event count at 13 (0x0d — the
  AVRCP 1.3 maximum):
    0x5e56: cmp r4, #0xd   ; if event count > 13...
    0x5e5a: bls +4
    0x5e5c: movs r4, #0xd  ; ...cap to 13

  This prevents Y1 from reporting event 14 (0x0e in MTK's numbering), which is
  the signal that unlocks AVRCP 1.4 on the car CT. Without event 14 in the
  GetCapabilities response, the car treats Y1 as AVRCP 1.3 and never sends
  REGISTER_NOTIFICATION — cardinality stays 0.
  Patches raise the cap from 13 (0x0d) to 14 (0x0e).

  Note: this cap was confirmed as NOT the root cause of cardinality:0 in isolation
  (patched + flashed without the SDP fix, cardinality remained 0 because the car
  was negotiating 1.3 from the SDP advertisement). These patches become meaningful
  once the SDP shows Version: 0x0104 via patch_mtkbt.py.

Usage:
    python3 patch_libextavrcp_jni.py libextavrcp_jni.so
    python3 patch_libextavrcp_jni.py libextavrcp_jni.so --output /tmp/libextavrcp_jni.so.patched
    python3 patch_libextavrcp_jni.py libextavrcp_jni.so --verify-only

Deploy:
    adb push output/libextavrcp_jni.so.patched /system/lib/libextavrcp_jni.so
    adb reboot
"""

import argparse
import hashlib
import sys
from pathlib import Path

STOCK_MD5  = "fd2ce74db9389980b55bccf3d8f15660"
OUTPUT_MD5 = "6c348ed9b2da4bb9cc364c16d20e3527"

PATCHES = [
    {
        "name": "sdpfeature: mov r5,r3 -> movs r5,#0x23  [defense-in-depth]",
        "offset": 0x3764,
        "before": bytes([0x1d, 0x46]),
        "after":  bytes([0x23, 0x25]),
    },
    {
        "name": "g_tg_feature: movs r0,#1 -> movs r4,#0x0e  [force AVRCP 1.4, defense-in-depth]",
        "offset": 0x37a8,
        "before": bytes([0x01, 0x20]),
        "after":  bytes([0x0e, 0x24]),
    },
    {
        "name": "CONNECT_CNF version cap: cmp r4,#0xd -> cmp r4,#0xe",
        "offset": 0x5e56,
        "before": bytes([0x0d, 0x2c]),
        "after":  bytes([0x0e, 0x2c]),
    },
    {
        "name": "CONNECT_CNF version cap: movs r4,#0xd -> movs r4,#0xe",
        "offset": 0x5e5c,
        "before": bytes([0x0d, 0x24]),
        "after":  bytes([0x0e, 0x24]),
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
        print(f"  [{'OK' if r['ok'] else 'FAIL'}] 0x{r['offset']:04x}  {r['name']}")
        if not r["ok"]:
            print(f"          expected: {r['before'].hex(' ')}")
            print(f"          actual:   {r['actual'].hex(' ')}")
    print("-" * 72)


def main():
    parser = argparse.ArgumentParser(
        description="Patch stock libextavrcp_jni.so for AVRCP 1.4"
    )
    parser.add_argument("input", help="Path to stock libextavrcp_jni.so")
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

    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / "libextavrcp_jni.so.patched"
    output_path.write_bytes(data)
    output_md5 = md5(data)

    print(f"\nOutput: {output_path}")
    print(f"MD5:    {output_md5}",  end="")
    print(f"  ({'OK' if output_md5 == OUTPUT_MD5 else 'MISMATCH — expected ' + OUTPUT_MD5})")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/lib/libextavrcp_jni.so")
    print(f"  adb reboot")


if __name__ == "__main__":
    main()
