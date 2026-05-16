#!/usr/bin/env python3
"""
patch_mtkbt.py — SDP shape + AV/C op_code dispatch against the stock mtkbt
Bluetooth daemon. Shapes the served AVRCP TG SDP record to AVRCP 1.3 / AVCTP
1.2 (V1+V2), A2DP/AVDTP 1.3 (V3+V4), drops the 1.4 Browse PSM advertisement
(V7), clears the 1.4 GroupNavigation feature bit (V8), inserts a 0x0100
ServiceName attribute (S1), reroutes the daemon to the v=14 SDP template
(V6), force-emits PASSTHROUGH dispatch for all AV/C frames (P1), and
best-effort aliases AVDTP sig 0x0c → 0x02 (V5).

Per-patch byte-level reference (offsets, before/after, rationale, ICS row
coverage, spec citations): docs/PATCHES.md.

Pairs with patch_libextavrcp_jni.py (trampoline chain handles the
1.3-COMMAND response side) and patch_mtkbt_odex.py (Java-side flag flips
+ cardinality NOPs).
"""

import argparse
import hashlib
import os
import struct
import sys
from pathlib import Path

STOCK_MD5         = "3af1d4ad8f955038186696950430ffda"
OUTPUT_MD5        = "2b0bffeb6d29ff2ba75cf811688ec0ef"

DEBUG_LOGGING     = os.environ.get("KOENSAYR_DEBUG", "") == "1"
OUTPUT_DEBUG_MD5  = OUTPUT_MD5

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
        # mtkbt's internal AVRCP activation handler (`fcn.00010d00`) hardcodes
        # `movs r3, #0xa` and writes 10 to the avrcp_state struct's activeVersion
        # field — overriding whatever activate_req's caller supplied (which F1
        # makes 14 via Java's getPreferVersion). The btlog line
        #   "AVRCP register activeVersion:10"
        # surfaces this override; downstream version-gated branches (compiled-
        # for-1.0 response builders, "sdp 1.0 target role" log, and the
        # 1.3-class wire-shape selector) all read this byte. Patching the
        # immediate to 14 aligns the internal activeVersion with F1.
        "name":   "[V6] AVRCP internal activeVersion 10->14 (override hardcoded in activation handler)",
        "offset": 0x00010dca,
        "before": bytes([0x0a, 0x23]),  # movs r3, #0xa
        "after":  bytes([0x0e, 0x23]),  # movs r3, #0xe
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
        # Wire-correct by decoupling: the response builder fcn.000ae418
        # reads the sig_id byte from per-transaction state (txn->[0xe],
        # populated by the request parser at RX time), not from the
        # dispatch handler. So the response carries sig_id=0x0c matching
        # the request, even though dispatch routes through the sig 0x02
        # handler. See docs/BT-COMPLIANCE.md §9.13 and
        # docs/INVESTIGATION.md Trace #16.
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
        # V6 routes the SDP record builder to a different served record whose
        # entry slot at vaddr 0xfa794 advertises attr 0x000d
        # AdditionalProtocolDescriptorList (Browse PSM 0x001b). The attribute
        # is introduced in AVRCP 1.4 §8 Table 8.2 (conditional on
        # SupportedFeatures bit 6 "Supports browsing"); AVRCP 1.3 §6 Table 6.2
        # does not list it. V7 swaps this slot for a 0x0100 ServiceName entry
        # (analogous to S1 for the other table) reusing the same "Advanced
        # Audio" string. Net wire: drops Browse advertisement, restores
        # ServiceName presence.
        "name":   "[V7] 0x000d Browse PSM -> 0x0100 ServiceName  AVRCP 1.3 record entry slot",
        "offset": 0x0f9798,
        # stock entry: attr=0x000d, len=0x14, ptr=0x0eba12 (-> AdditionalProtocolDescList Browse PSM 0x1b)
        "before": entry(0x000d, 0x0014, 0x000eba12),
        # patched: attr=0x0100, len=0x11, ptr=0x0eb9ce (-> SDP TEXT_STR_8 "Advanced Audio\\0")
        "after":  entry(0x0100, 0x0011, 0x000eb9ce),
    },
    {
        # SupportedFeatures byte stream LSB. V6's served record uses the byte
        # stream at 0xeba4c (`09 00 21`) — bit 5 set is "Group Navigation"
        # per AVRCP 1.3 §6 Table 6.2 (conditional on bit 0 Cat 1 being set).
        # Y1 ships no Group Navigation PASSTHROUGH handler, so per Table 6.2's
        # note ("the bits for supported categories are set to 1; others are
        # set to 0") this bit should be 0. V8 clears it so the advertised
        # mask is 0x0001 (Category 1: Player/Recorder only).
        "name":   "[V8] SupportedFeatures 0x0021 -> 0x0001  clear Group Navigation bit (unimplemented)",
        "offset": 0x0eba4e,
        "before": bytes([0x21]),
        "after":  bytes([0x01]),
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
    {
        # M1 — RegisterNotification response wire ctype: route the JNI's
        # reasonCode through to mtkbt's AV/C ctype emitter.
        #
        # Stock mtkbt's per-event RegNotif response packetFrame builder
        # (fcn.000121d8) reads ctxt[8] and compares against 1 to choose
        # between INTERIM (ctype 0x0F at 0x12238) and CHANGED (ctype 0x0D
        # at 0x12244) branches. The JNI's `btmtk_avrcp_send_reg_notievent_*_rsp`
        # helpers in `libextavrcp.so` marshal the reasonCode argument
        # (REASON_INTERIM=0x0F / REASON_CHANGED=0x0D) into IPC payload
        # byte 8 (matches the `strb.w r7, [sp, #12]` at the cardinality=0
        # path of every helper; sp+12 maps to payload+8 because the helper's
        # 40-byte buffer sits at sp+4). Stock mtkbt reads the correct byte
        # but compares against 1, so 0x0F and 0x0D both fail the cmp and
        # the dispatch always lands on the CHANGED branch — wire ctype is
        # 0x0D for every RegNotif response, regardless of which reasonCode
        # the trampoline passes.
        #
        # M1 widens the cmp from 1 to 0x0F at file offset 0x12230:
        #   0x12230: cmp r1, 1   -> cmp r1, 0xF
        #            01 29        -> 0f 29
        # After M1:
        #   ctxt[8] == 0x0F → INTERIM branch → wire ctype 0x0F INTERIM
        #     (T2 / extended_T2 / T8 first-response arms)
        #   ctxt[8] != 0x0F → CHANGED branch → wire ctype 0x0D CHANGED
        #     (T5 / T9 edge emits)
        # Spec-compliant per AVRCP 1.3 §6.7.1: INTERIM on first response per
        # registration, CHANGED on subsequent value updates without
        # re-registration.
        "name":   "[M1] RegNotif INTERIM/CHANGED discriminator: cmp ctxt[8] against 0x0F (mtkbt 0x12230)",
        "offset": 0x12230,
        "before": bytes([0x01, 0x29]),  # cmp r1, 1
        "after":  bytes([0x0f, 0x29]),  # cmp r1, 0xF
    },
    {
        # M2 — TG-side outbound-frame drop gate at fcn.0x6d048.
        #
        # fcn.0x6d048 is the outbound-frame builder reached from the chain
        # `fcn.0xf0bc → fcn.0xed50 → fcn.0x6d048 → fcn.0x6df20 → fcn.0xae5e4`
        # for short-frame AVRCP responses (PSTAT, REACHED_END/START, batt
        # status — anything under the L2CAP MTU). At file offset 0x6d068
        # it calls fcn.0x6ccdc (doubly-linked-list contains check) against
        # g_active_conn_list at *(0xf99XX). If our conn isn't in the list,
        # the function returns 0xd (drop) WITHOUT building or sending the
        # wire frame — and the caller (fcn.0xf0bc) treats this as success
        # via `cmp r5, 2; bne 0xf208` so the drop is silent.
        #
        # Empirically (dual-kia-20260515-2215, Trace #40), the list is
        # populated whenever the chip is ready and emptied when the chip
        # is mid-write. Under A2DP saturation the conn is in the list only
        # ~10-20% of the time, so 80% of our T9 emits drop here. AVRCP_
        # SendMessage's return path doesn't surface this because send()
        # already succeeded (the datagram was queued into mtkbt's IPC recv
        # buffer); the drop happens further down inside mtkbt itself.
        #
        # M2 NOPs the `beq 0x6d0e0` at file offset 0x6d06e (2 bytes,
        # `37 d0` → `00 bf`). After M2, fcn.0x6d048 unconditionally builds
        # the wire frame and tail-calls fcn.0x6df20 — bypassing the
        # "is-this-conn-active" check. Safe because:
        #   - The list state was a chip-readiness heuristic, not a
        #     correctness check. Conn pointer is stable across the BT
        #     pairing session (per Trace #40's enableNative RE).
        #   - The downstream send (fcn.0xae5e4 / fcn.0xae418) handles
        #     its own per-channel busy state.
        "name":   "[M2] Outbound-frame drop bypass: NOP gate 1 list-contains check (mtkbt 0x6d06e)",
        "offset": 0x6d06e,
        "before": bytes([0x37, 0xd0]),  # beq 0x6d0e0 (drop with rc=0xd)
        "after":  bytes([0x00, 0xbf]),  # nop
    },
    {
        # M3 — TG-side chip-busy gate at fcn.0x6df20.
        #
        # fcn.0x6df20 is the second-stage outbound-frame send, tail-called
        # from M2's site. At file offset 0x6df3a it tests `ctx[0xf2]`
        # (chip-write busy flag). If set, returns 0xb (drop). The flag is
        # SET at 0x6df42 just before the chip-send tail-call to
        # fcn.0xae5e4, and CLEARED at fcn.0x6d9b8:0x6da10 in the
        # send-completion handler when the chip ACKs the write.
        #
        # Empirically (dual-kia-20260515-2215, Trace #40), this gate
        # combines with M2's gate 1 to produce the ~18% delivery rate
        # for PSTAT and ~9% for POS. Between mtkbt initiating a chip-write
        # and the chip's ACK event, the busy flag is set and all new
        # emits drop silently.
        #
        # M3 NOPs the SET at 0x6df42 (4 bytes, `84 f8 f2 00` → two NOPs).
        # The CHECK at 0x6df3a stays intact but always reads 0 (since the
        # flag never gets set), so the gate never trips. Safer than NOPing
        # the check itself because:
        #   - mtkbt's IPC dispatcher is single-threaded; fcn.0xae5e4's
        #     downstream chain (fcn.0xae418 → fcn.0x50918 → mtk_bt_write)
        #     is synchronous (blocking UART write). No concurrent emits
        #     can race on the per-channel state inside fcn.0xae5e4.
        #   - The completion handler (fcn.0x6d9b8) still clears the flag
        #     on ACK events — harmless no-op since the flag is already 0.
        #
        # Trade-off: under A2DP saturation, our T9 emits may now be
        # SLOWER on average (each blocks until mtk_bt_write completes,
        # whereas before they'd drop fast and return). But total
        # throughput is higher: every emit reaches the wire instead of
        # 18% of them.
        "name":   "[M3] Chip-busy gate bypass: NOP set-busy-flag (mtkbt 0x6df42)",
        "offset": 0x6df42,
        "before": bytes([0x84, 0xf8, 0xf2, 0x00]),  # strb.w r0, [r4, #0xf2]
        "after":  bytes([0x00, 0xbf, 0x00, 0xbf]),  # nop; nop
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
