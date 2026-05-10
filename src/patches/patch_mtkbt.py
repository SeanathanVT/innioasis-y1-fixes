#!/usr/bin/env python3
"""
patch_mtkbt.py — SDP / VENDOR_DEPENDENT routing patches against stock mtkbt.

Stock md5:  3af1d4ad8f955038186696950430ffda
Output md5: a37d56c91beb00b021c55f7324f2cc09

Four byte-level patches against the SERVED AVRCP TG record (Group D, the
record that actually lands on the wire after mtkbt's last-wins merge) plus
the AV/C op_code dispatcher. The goal is (1) make permissive CTs and other AVRCP
1.3+ controllers engage with the record and start sending VENDOR_DEPENDENT
commands and (2) route those commands into the JNI's msg 519 emit path so
the libextavrcp_jni.so trampoline chain (patch_libextavrcp_jni.py) can
synthesise the AVRCP 1.3 responses.

Targets a spec-conformant AVRCP 1.3 SDP record shape: the standard set of
attributes plus a 0x0100 ServiceName attribute that stock mtkbt lacks.

V1 — AVRCP 1.0 -> 1.3 (served AVRCP TG ProfileDescList LSB)
V2 — AVCTP 1.0 -> 1.2 (served AVRCP TG ProtocolDescList AVCTP version LSB)
V3 — A2DP 1.0 -> 1.3 (served A2DP Source ProfileDescList LSB)
V4 — AVDTP 1.0 -> 1.3 (served A2DP Source ProtocolDescList AVDTP version LSB)
V5 — AVDTP sig 0x0c GET_ALL_CAPABILITIES dispatch alias (best-effort workaround)
S1 — Replace the 0x0311 SupportedFeatures attribute table entry with a
     0x0100 ServiceName entry pointing at the existing "Advanced Audio"
     SDP string at file offset 0x0eb9ce. This sacrifices the SupportedFeatures
     attribute on the wire (peers see no 0x0311); the known-working AVRCP 1.3
     reference advertises features=0x0001 but permissive CTs engage without
     strictly requiring the attribute.
     The string content "Advanced Audio" is reused from mtkbt's existing A2DP
     ServiceName — peers don't validate ServiceName content; they just need
     the attribute present so the record passes structural sanity checks.
P1 — Force fn 0x144bc's op_code dispatch to always take the PASSTHROUGH branch
     (which calls bl 0x10404 → emits msg 519 CMD_FRAME_IND to JNI). gdbserver
     traces confirmed PASSTHROUGH (op_code=0x7c) flows through fn 0x144bc's
     b.n 0x14528 path → bl 0x10404 and produces msg 519, while VENDOR_DEPENDENT
     (op_code=0x00) takes the bcc → bl 0x11374 path which only logs. The patch
     replaces the first cmp at 0x144e8 with an unconditional b.n 0x14528,
     skipping the op_code check entirely. All inbound AV/C frames now take the
     emit path; Y1MediaBridge can parse the frame and respond.

     Risk: the bl 0x10404 path may interpret VENDOR_DEPENDENT frame bytes as
     PASSTHROUGH, producing malformed responses. Worst case is mtkbt emits
     a NOT_IMPLEMENTED reply to the peer (which is what currently happens
     anyway). Best case msg 519 fires with the inbound bytes preserved and
     Y1MediaBridge handles the rest.

Pairs with patch_libextavrcp_jni.py (handles the inbound-COMMAND response
side via the trampoline chain in libextavrcp_jni.so) and patch_mtkbt_odex.py
(F1 / F2 + cardinality NOP Java-side patches).

Usage:
    python3 patch_mtkbt.py mtkbt
    python3 patch_mtkbt.py mtkbt --output /tmp/mtkbt.patched
    python3 patch_mtkbt.py mtkbt --verify-only
"""

import argparse
import hashlib
import os
import struct
import sys
from pathlib import Path

STOCK_MD5         = "3af1d4ad8f955038186696950430ffda"
OUTPUT_MD5        = "51a9881d5c5c21b375880cfcf8e23792"

# Build-time debug toggle. `apply.bash --debug` exports KOENSAYR_DEBUG=1.
# Placeholder — mtkbt is a stripped-down ARM ELF without symbols or a Java
# layer; current patches are byte-level SDP-record edits. If we ever need
# to instrument the daemon's AVRCP dispatch, we'd inject a syscall write()
# stub from the patcher conditional on this flag. Once the debug build
# diverges from release, pin a separate hash in OUTPUT_DEBUG_MD5.
DEBUG_LOGGING     = os.environ.get("KOENSAYR_DEBUG", "") == "1"
OUTPUT_DEBUG_MD5  = OUTPUT_MD5

# Effective expected output MD5 for the current invocation.
EXPECTED_OUTPUT_MD5 = OUTPUT_DEBUG_MD5 if DEBUG_LOGGING else OUTPUT_MD5

# 12-byte descriptor table entry: attrID:LE16, len:LE16, ptr:LE32, zeros:LE32
def entry(attr_id: int, length: int, ptr: int) -> bytes:
    return struct.pack("<HHII", attr_id, length, ptr, 0)

PATCHES = [
    {
        "name":   "[V1] AVRCP 1.0->1.3 LSB  Group D ProfileDescList (served)",
        "offset": 0x0eba58,
        "before": bytes([0x00]),
        "after":  bytes([0x03]),
    },
    {
        "name":   "[V2] AVCTP 1.0->1.2 LSB  Group D ProtocolDescList (served)",
        "offset": 0x0eba6d,
        "before": bytes([0x00]),
        "after":  bytes([0x02]),
    },
    {
        # A2DP 1.0 -> 1.3 in the served A2DP Source SDP record's
        # BluetoothProfileDescriptorList entry (attribute 0x0009): UUID 0x110D
        # AdvancedAudioDistribution + uint16 version. Per A2DP 1.3 §5.3
        # Figure 5.1 the Mandatory version value is 0x0103. Pairs with V4 —
        # peers consult our advertised AVDTP version before GAVDP setup
        # per A2DP §3.1, so both bumps must ship together to avoid
        # asymmetric advertisement.
        "name":   "[V3] A2DP 1.0->1.3 LSB  A2DP Source ProfileDescList (served)",
        "offset": 0x0eb9f2,
        "before": bytes([0x00]),
        "after":  bytes([0x03]),
    },
    {
        # AVDTP 1.0 -> 1.3 in the served A2DP Source SDP record's
        # ProtocolDescriptorList (attribute 0x0004): L2CAP / PSM=0x0019
        # AVDTP, then UUID 0x0019 AVDTP + uint16 version. Per A2DP 1.3 §5.3
        # the Mandatory AVDTP version is 0x0103. AVDTP 1.3 ICS Table 14
        # confirms only Basic Transport Service Support is Mandatory for
        # the Source role; Delay Reporting / GET_ALL_CAPABILITIES are
        # Optional at the AVDTP layer.
        "name":   "[V4] AVDTP 1.0->1.3 LSB  A2DP Source ProtocolDescList (served)",
        "offset": 0x0eba09,
        "before": bytes([0x00]),
        "after":  bytes([0x03]),
    },
    {
        # Best-effort dispatch alias: redirect AVDTP signal 0x0c
        # (GET_ALL_CAPABILITIES) through the existing sig 0x02 (GET_CAPABILITIES)
        # handler. This is a structural workaround, not a real GET_ALL_CAPABILITIES
        # implementation — the response we emit is the sig 0x02 capability list,
        # which per AVDTP V13 §8.8 is a wire-compatible SUBSET of the sig 0x0c
        # response (no extended Service Capabilities). For an SBC-only Source
        # this matches what we'd advertise anyway.
        #
        # Edits one halfword in the dispatcher's TBH jump table at file 0xaa81e.
        # Entry 11 (sb = sig_id - 1 = 11 → sig 0x0c) currently routes to 0xab4de
        # (a stub that writes BAD_LENGTH-style error code 0x06 and calls the
        # error-response sender at fcn.000af4cc). Re-pointing to 0xaa924 routes
        # the same dispatch through the full GET_CAPABILITIES handler.
        #
        # TBH formula: target = 0xaa81e + 2 * halfword
        #   stock:   halfword 0x0660 → target 0xab4de (sig 0x0c stub)
        #   patched: halfword 0x0083 → target 0xaa924 (sig 0x02 handler)
        #
        # Wire-correctness is plausible but not statically proven — the
        # AVDTP wire-frame TX site that writes the response sig_id byte
        # is not yet localised. Worst case (peer rejects) is no worse than
        # stock's BAD_LENGTH error path. See docs/BT-COMPLIANCE.md §9.13
        # and docs/INVESTIGATION.md Trace #13c + #15.
        "name":   "[V5] sig 0x0c -> sig 0x02 dispatch alias  AVDTP TBH jump table",
        "offset": 0x0aa834,
        "before": bytes([0x60, 0x06]),
        "after":  bytes([0x83, 0x00]),
    },
    {
        "name":   "[S1] 0x0311 SupportedFeatures -> 0x0100 ServiceName  Group D entry slot",
        "offset": 0x0f97ec,
        # stock entry: attr=0x0311, len=3, ptr=0x0eba59 (-> uint16 0x0001)
        "before": entry(0x0311, 0x0003, 0x000eba59),
        # patched: attr=0x0100, len=0x11, ptr=0x0eb9ce (-> SDP TEXT_STR_8 "Advanced Audio\\0")
        "after":  entry(0x0100, 0x0011, 0x000eb9ce),
    },
    {
        # `cmp r3, #0x30` at 0x144e8 → `b.n 0x14528` (unconditional). Bypasses
        # the op_code dispatch in fn 0x144bc so all inbound AV/C frames reach
        # the bl 0x10404 PASSTHROUGH-emit path → msg 519 CMD_FRAME_IND fires
        # for VENDOR_DEPENDENT frames too.
        # Thumb encoding: cmp r3, #0x30 = 0x2b30 (LE bytes 30 2b)
        #                 b.n +0x3c    = 0xe01e (LE bytes 1e e0)
        # Branch target at 0x14528 = current PC (0x144ec) + 0x3c.
        "name":   "[P1] cmp r3, #0x30 -> b.n 0x14528  force msg 519 emit path in fn 0x144bc",
        "offset": 0x144e8,
        "before": bytes([0x30, 0x2b]),
        "after":  bytes([0x1e, 0xe0]),
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
    # Quiet when everything verifies — print a one-line summary. The full
    # per-site listing is only needed for diagnosis when something fails.
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
        description="Minimum SDP patches for stock mtkbt — AVRCP 1.3 reference SDP shape + ServiceName"
    )
    parser.add_argument("input", help="Path to stock mtkbt binary")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path (default: output/mtkbt.patched)")
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

    # Already-at-expected-output fast path. MD5 over the whole file is
    # strictly stronger evidence than verifying a handful of patch sites,
    # so when the input already hashes to the expected output for the
    # current build mode (release or debug) there's nothing to do.
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

    # Site-level verification is only informative when MD5 alone isn't
    # sufficient: alternate stock build (--skip-md5) or development mode
    # where the expected output MD5 isn't pinned yet. On the normal happy
    # path the input-MD5 and output-MD5 checks cover every byte in the file.
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

    # Post-patch site verification fires either when we're already in a
    # site-aware mode (developer / alternate stock) or as a diagnostic when
    # the produced output doesn't hash to the pinned expected value.
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
        output_path = output_dir / "mtkbt.patched"
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
    print(f"  adb push {output_path} /system/bin/mtkbt")
    print(f"  adb shell chmod 755 /system/bin/mtkbt")
    print(f"  adb reboot")
    print(f"  sdptool browse --xml <Y1_BT_ADDR>   # expect (AVRCP TG record): AVCTP 0x0102, AVRCP 0x0103, attr 0x0100 present")
    print(f"                                       # expect (A2DP Source record): AVDTP 0x0103, A2DP 0x0103")

    if output_md5_mismatch and not args.skip_md5:
        print("\nERROR: output MD5 doesn't match expected. Output was written but"
              " the patcher's expected hash is stale or the patch logic diverged."
              " Pass --skip-md5 to suppress.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
