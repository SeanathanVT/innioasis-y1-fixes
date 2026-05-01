#!/usr/bin/env python3
"""
patch_mtkbt.py — Patch stock mtkbt binary → mtkbt.patched

Stock md5:  3af1d4ad8f955038186696950430ffda
Output md5: (regenerated on each build — see script output)

--- Descriptor table structure (key finding) ---

The mtkbt descriptor table at file offset 0x0f9774 has three AVRCP service record
groups, each a contiguous run of 5-6 entries (attrID LE16, len LE16, ptr LE32,
zeros LE32). The groups are:

  Group 1 (entries [0]-[5], TG record A):
    ServiceClassIDList ptr=0x0eba38 → {UUID(AV Remote Target 0x110c)}
    ProtocolDescList   ptr=0x0eba5c → L2CAP(PSM=23) + AVCTP(1.0→1.3)  [shared w/ Group 2]
    AdditionalProtocol ptr=0x0eba12 → L2CAP(PSM=23) + AVCTP(1.0→1.3)  [browsing descriptor]
    ProfileDescList    ptr=0x0eba6e → AV Remote(0x110e) v1.3→1.4        [entry index 13]
    SupportedFeatures  ptr=0x0eba4c → 0x0021 (Category1 + GroupNavigation)

  Group 2 (entries [6]-[10], TG record B — LAST WINS for TG):
    ServiceClassIDList ptr=0x0eba38 → {UUID(AV Remote Target 0x110c)}   [same blob as Group 1]
    ProtocolDescList   ptr=0x0eba5c → L2CAP(PSM=23) + AVCTP(1.0→1.3)   [same blob as Group 1]
    ProfileDescList    ptr=0x0eba4f → AV Remote(0x110e) v1.0→1.4        [entry index 18, served by sdptool]
    SupportedFeatures  ptr=0x0eba59 → 0x0001 (Category1 only)

  Group 3 (entries [11]-[15], CT record):
    ServiceClassIDList ptr=0x0eba78 → {UUID(AV Remote 0x110e)}
    ProtocolDescList   ptr=0x0eba26 → L2CAP(PSM=23) + AVCTP(1.0→1.3)
    ProfileDescList    ptr=0x0eba42 → AV Remote(0x110e) v1.0→1.4        [entry index 23]
    SupportedFeatures  ptr=0x0eba0f → 0x000f (Category1-4)

Note: AttrID=0x0311 (SupportedFeatures) IS registered in all three groups. The
earlier "ELIMINATED" note claiming "AttrID 0x0311 not registered" was incorrect —
it was based on a false negative from testing a non-live patch site. All three
0x0311 entries have non-zero values in the descriptor table.

--- Eliminated patches (do not restore) ---

  ELIMINATED — old #1 (0xeba1d): PSM byte — unrelated to version.
  ELIMINATED — old #5 (0x0f97b2): descriptor table flags = element size, not control.
  ELIMINATED — old #7, #8 (0x00012d7c, 0x00012d84): FUN_00022cec, not on SDP path.
  ELIMINATED — old #9 (0x0000ead4): FUN_000108d0 ignores r1 parameter.
  ELIMINATED — old #10 (0x000afd6a): version sink downstream of SDP construction.

--- Patches in this script ---

  B1-B3 — AVCTP version in ProtocolDescList / AdditionalProtocol blobs:
    Stock mtkbt advertises AVCTP 1.0 (0x0100) in all three AVCTP-bearing blobs.
    AVRCP 1.4 requires AVCTP 1.3 (0x0103). Three LSBs are patched 0x00 → 0x03:

      0x0eba6d  Groups 1 & 2 shared ProtocolDescList (TG control channel)
      0x0eba37  Group 3 ProtocolDescList (CT control channel)
      0x0eba25  Group 1 AdditionalProtocol (browsing channel descriptor)

  C1-C3 — AVRCP profile version in ProfileDescList blobs (all three groups):
    The SDP stack uses last-wins semantics across entries; all three are patched
    to 1.4 to guarantee the correct value regardless of which entry is served:

      entry[23] ptr=0x0eba42  minor version at 0x0eba4b  stock: 0x00  -> 0x04
      entry[18] ptr=0x0eba4f  minor version at 0x0eba58  stock: 0x00  -> 0x04
      entry[13] ptr=0x0eba6e  minor version at 0x0eba77  stock: 0x03  -> 0x04

    Old patches #2 (0xeba4b: 00->03) and #3 (0xeba58: 00->03) covered entries
    [23] and [18] respectively; both were previously mislabelled "eliminated."
    Both are now set to 1.4.

  A1 — Runtime SDP MOVW at 0x38BFC: runtime struct version
    The SDP init function at 0x38AB0-0x38C74 also writes the version to a
    runtime SDP struct via STRH.W r7,[r3,#72] at 0x38C02. MOVW r7,#0x0301
    (bytes: 40 f2 01 37) is patched to MOVW r7,#0x0401 (40 f2 01 47).
    Belt-and-suspenders alongside the blob patches.

  D1 — Registration guard NOP at 0x38C6C: BNE 0x38C76 → NOP
    Forces the SDP init function to always register the AVRCP TG record
    (CMP r0, r5 guard was never true, leaving the record unregistered).

  E3-E4 — AVRCP TG SupportedFeatures bitmask (the served value on the wire).
    sdptool browse against post-D1 mtkbt confirms AttrID=0x0311 IS on the wire
    inside the AVRCP TG record (UUID 0x110c), but the served value is 0x0001
    (Cat1 only). 1.4 controllers see ProfileVersion=1.4 with a feature bitmask
    consistent with 1.0, treat the advertiser as inconsistent, and skip
    REGISTER_NOTIFICATION. AVRCP 1.4 TG baseline (matching AOSP Bluedroid) is
    0x0033 = bits {0,1,4,5} = Cat1 + Cat2 + PlayerApplicationSettings +
    GroupNavigation. Browsing (bit 6) is deliberately omitted — the
    AdditionalProtocolDescriptorList isn't on the wire (Group 1 only, Group 2
    wins the merge), so claiming Browsing without serving the descriptor would
    re-introduce inconsistency.

      0x0eba5b  Group 2 TG SupportedFeatures LSB  0x01 -> 0x33  [served]
      0x0eba4e  Group 1 TG SupportedFeatures LSB  0x21 -> 0x33  [defense-in-depth]

Usage:
    python3 patch_mtkbt.py mtkbt
    python3 patch_mtkbt.py mtkbt --output /tmp/mtkbt.patched
    python3 patch_mtkbt.py mtkbt --verify-only

Deploy:
    adb push output/mtkbt.patched /system/bin/mtkbt
    adb shell chmod 755 /system/bin/mtkbt
    adb reboot
    sdptool browse <Y1_BT_ADDR>   # expect: AVCTP uint16: 0x0103, AV Remote Version: 0x0104
    logcat | grep -E 'tg_feature|ct_feature|cardinality|CONNECT_CNF'
"""

import argparse
import hashlib
import sys
from pathlib import Path

STOCK_MD5  = "3af1d4ad8f955038186696950430ffda"
OUTPUT_MD5 = "b17bdf5448fdae68c1d477626190e63e"

PATCHES = [
    # B1-B3: AVCTP version 1.0 -> 1.3 in all registered AVCTP-bearing blobs.
    # AVRCP 1.4 requires AVCTP 1.3; the LSB byte at each offset is the minor version.
    {
        "name":   "0x0eba6d: AVCTP 1.0->1.3 LSB  Groups 1&2 ProtocolDescList  [B1]",
        "offset": 0x0eba6d,
        "before": bytes([0x00]),
        "after":  bytes([0x03]),
    },
    {
        "name":   "0x0eba37: AVCTP 1.0->1.3 LSB  Group 3 CT ProtocolDescList  [B2]",
        "offset": 0x0eba37,
        "before": bytes([0x00]),
        "after":  bytes([0x03]),
    },
    {
        "name":   "0x0eba25: AVCTP 1.0->1.3 LSB  Group 1 AdditionalProtocol   [B3]",
        "offset": 0x0eba25,
        "before": bytes([0x00]),
        "after":  bytes([0x03]),
    },
    # C1-C3: AVRCP profile version in ProfileDescList blobs, all three groups.
    # All patched to 1.4 — last-wins entry wins regardless of which is served.
    {
        "name":   "0x0eba4b: AVRCP 1.x->1.4 LSB  entry[23] ProfileDescList  [C1]",
        "offset": 0x0eba4b,
        "before": bytes([0x00]),
        "after":  bytes([0x04]),
    },
    {
        "name":   "0x0eba58: AVRCP 1.x->1.4 LSB  entry[18] ProfileDescList  [C2 — served]",
        "offset": 0x0eba58,
        "before": bytes([0x00]),
        "after":  bytes([0x04]),
    },
    {
        "name":   "0x0eba77: AVRCP 1.3->1.4 LSB  entry[13] ProfileDescList  [C3]",
        "offset": 0x0eba77,
        "before": bytes([0x03]),
        "after":  bytes([0x04]),
    },
    # A1: Runtime SDP struct version patched via MOVW instruction.
    {
        "name":   "0x38BFC: MOVW r7,#0x0301 -> #0x0401  [A1 — runtime SDP struct]",
        "offset": 0x038BFC,
        "before": bytes([0x40, 0xf2, 0x01, 0x37]),
        "after":  bytes([0x40, 0xf2, 0x01, 0x47]),
    },
    # D1: NOP the runtime registration guard.
    #
    # The SDP init function (0x38AB0-0x38C74) builds the AVRCP TG SDP struct in r3,
    # then checks CMP r0, r5 (r5=0x111F) before executing the three writes that
    # complete registration:
    #
    #   0x38C6E: STR r3, [r1]    — links the struct into mtkbt's live SDP registry
    #   0x38C70: MOVS r0, #8     — success return value
    #   0x38C72: STRB r7, [r2]   — writes version status byte
    #
    # r0 is never 0x111F in normal operation, so BNE always branches to the skip
    # path (0x38C76: MOV r0, r4 / POP), leaving the AVRCP TG record unregistered.
    # Result: mtkbt returns tg_feature:0 ct_feature:0 in every CONNECT_CNF, and
    # peers never send REGISTER_NOTIFICATION (cardinality stays 0), regardless of
    # what the SDP blob advertises.
    #
    # Fix: replace BNE with NOP — the struct is always registered.
    {
        "name":   "0x38C6C: BNE 0x38C76 -> NOP  registration guard bypass  [D1]",
        "offset": 0x038C6C,
        "before": bytes([0x03, 0xd1]),
        "after":  bytes([0x00, 0xbf]),
    },
    # E3-E4: AVRCP TG SupportedFeatures bitmask in the served SDP record.
    # Wire-confirmed: post-D1 sdptool browse shows AttrID=0x0311 = 0x0001 (Cat1
    # only) in the AVRCP TG record. 1.4 controllers see ProfileVersion=1.4 + a
    # 1.0-shape bitmask, treat the advertiser as inconsistent, and skip
    # REGISTER_NOTIFICATION. 0x0033 = Cat1 + Cat2 + PAS + GroupNav — the AVRCP
    # 1.4 TG baseline matching AOSP Bluedroid. Browsing bit (6) is omitted
    # because AdditionalProtocolDescriptorList isn't served on the wire
    # (Group 1 has it, Group 2 wins the merge).
    {
        "name":   "0x0eba5b: SupportedFeatures 0x0001->0x0033  Group 2 TG (served)  [E3]",
        "offset": 0x0eba5b,
        "before": bytes([0x01]),
        "after":  bytes([0x33]),
    },
    {
        "name":   "0x0eba4e: SupportedFeatures 0x0021->0x0033  Group 1 TG (defense)  [E4]",
        "offset": 0x0eba4e,
        "before": bytes([0x21]),
        "after":  bytes([0x33]),
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

    if OUTPUT_MD5 is None:
        out_tag = f"[set OUTPUT_MD5 = \"{output_md5}\"]"
    elif output_md5 == OUTPUT_MD5:
        out_tag = "[OK — matches expected]"
    else:
        out_tag = f"[MISMATCH — expected {OUTPUT_MD5}]"

    print(f"\nOutput: {output_path}  ({len(data):,} bytes)")
    print(f"MD5:    {output_md5}  {out_tag}")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/bin/mtkbt")
    print(f"  adb shell chmod 755 /system/bin/mtkbt")
    print(f"  adb reboot")
    print(f"  sdptool browse <Y1_BT_ADDR>   # expect: AVCTP 0x0103, AV Remote Version: 0x0104")
    print(f"  logcat | grep -E 'tg_feature|ct_feature|cardinality|CONNECT_CNF'")


if __name__ == "__main__":
    main()
