#!/usr/bin/env python3
"""
patch_libextavrcp_jni.py — AVRCP TG/Target trampoline chain patched into
libextavrcp_jni.so so this firmware (where Java-side AVRCP is a no-op stub)
can answer permissive CTs' metadata queries directly from native code.

Pairs with patch_mtkbt.py's P1 patch which routes inbound VENDOR_DEPENDENT
AV/C commands through msg 519 with size=9.

Stock binary md5:  fd2ce74db9389980b55bccf3d8f15660
Output md5:        (recomputed each build — set OUTPUT_MD5 below)

--- Background (per docs/INVESTIGATION.md Trace #12 + docs/PROXY-BUILD.md) ---

The JNI's msg-519 receive function `_Z17saveRegEventSeqIdhh` (body at file
0x5f0c) dispatches inbound CMD_FRAME_IND on frame size:

  size == 3 → PASSTHROUGH path → btmtk_avrcp_send_pass_through_rsp + JNI->Java
  size == 8 → BT-SIG vendor check; on match calls JNIEnv->CallVoidMethodV
              (vtable offset 248) into a Java *Ind callback
  otherwise → "unknow indication" + default reject (msg 520 NOT_IMPLEMENTED)

P1 (in patch_mtkbt.py) routes VENDOR_DEPENDENT frames into the msg-519
emit path with size=9, so the JNI sees size!=8 and falls into the "unknow
indication" branch. We need to handle 1.3+ commands here, since mtkbt's own
dispatcher is compiled against AVRCP 1.0 and never invokes the response
builder for 1.3+ COMMANDs.

--- Trampoline chain (R1 + T1 + T2 stub + extended_T2 + T4 + T5 + T_charset + T_battery) ---

R1 — at file 0x6538: replace `bne.n 0x65bc; movs r5, #9` (40 d1 09 25)
     with `bl.w 0x7308` (00 f0 e6 fe). Branches to T1 for all size!=3 cases.

T1 — at 0x7308 (overwrites unused JNI debug method `testparmnum`, 40 of 48
     bytes): GetCapabilities (PDU 0x10) — answers with EVENT_TRACK_CHANGED
     in the supported-events list and falls through (b.w 0x72d4) to the
     T2 stub for everything else.

T2 stub — at 0x72d0 (overwrites unused JNI debug method `classInitNative`,
     8 of 48 bytes):
       0x72d0: classInitNative `return 0` stub (4 bytes preserves contract)
       0x72d4: b.w extended_T2 (4 bytes)
       0x72d8..0x72ff: padding (40 bytes; unreachable, kept zero)

extended_T2 — in LOAD #1 padding area, at vaddr derived from T4 layout.
     Handles RegisterNotification(EVENT_TRACK_CHANGED, PDU 0x31, event 0x02):
       1. Read first 8 bytes of /data/data/com.y1.mediabridge/files/y1-track-info
          (the track_id Y1MediaBridge wrote there). On read failure: 0xFF×8.
       2. Write [track_id (8) || transId (1) || pad (7)] to y1-trampoline-state
          (so T4 can later check whether the track has changed and use the
          right transId in any CHANGED notification it emits).
       3. Reply track_changed_rsp INTERIM with the current track_id.
     Other PDUs/events fall through to T4 (PDU 0x20) or 0x65bc (unknown).

T4 — in LOAD #1 padding at vaddr 0xac54.
     Handles GetElementAttributes (PDU 0x20):
       1. memset file buffer (776 B) on stack, read y1-track-info into it.
       2. Read y1-trampoline-state (16 B) into a state buffer on stack.
       3. If state[0..7] != file[0..7] (track changed since we last said so):
            - Emit track_changed_rsp CHANGED (iter19a: with arg1=0)
            - Update state[0..7] = file[0..7] and write back to state file
       4. Reply 3× get_element_attributes_rsp for Title (file+8) / Artist
          (file+264) / Album (file+520).
     iter19a: T4's pre-check now also dispatches PDU 0x17 → T_charset
     and PDU 0x18 → T_battery before falling through to "unknow indication".

T_charset — iter19a, in LOAD #1 padding past the T4/extended_T2/T5 blob.
     Handles InformDisplayableCharacterSet (PDU 0x17). Calls
     inform_charsetset_rsp via PLT 0x3588 with arg1=0 (success). Bare 8-byte
     ack frame; the spec doesn't require us to honor the CT's charset
     declaration, just to acknowledge it. A strict CT sends this once
     at connect; iter19a stops the previous msg=520 NOT_IMPLEMENTED reject
     that was likely degrading the strict CT's metadata-fetch behavior.

T_battery — iter19a, structurally identical to T_charset but for
     InformBatteryStatusOfCT (PDU 0x18) → battery_status_rsp via PLT 0x357c.
     CT notifies us of its battery state; we ack. Y1 has no CT-battery API
     surface to feed the value into; the ack alone is what the spec requires.

The T4 + extended_T2 + T5 + T_charset + T_battery blob is built
dynamically by _trampolines.py using a tiny Thumb-2 assembler
(_thumb2asm.py). LOAD #1's filesz/memsz is extended to cover the blob,
which lets the kernel map it as R+E at runtime — the 4276-byte
page-alignment gap between LOAD #1's stock end (0xac54) and LOAD #2's
start (0xbc08) is zero padding, so we can grow LOAD #1 freely up to
that limit.

--- History ---

iter4 (J1 cmp lr,#8 → cmp lr,#9 at 0x6526): rolled back. Routed
VENDOR_DEPENDENT through size==8 PASSTHROUGH dispatch and didn't reach
the right Java callback. See docs/INVESTIGATION.md Trace #12.

iter5..iter13: T1 + T2 + T4 progressively built. iter13 hardware-verified
Title + Artist + Album displayed on permissive CTs — but only for the first track:
permissive CTs cache metadata by TRACK_CHANGED INTERIM track_id, and our T2 always
sent 0xFF×8.

iter14/14b/14c: Y1MediaBridge wrote real metadata into a file; T4 reads
it. iter14b found the right path (/data/data/com.y1.mediabridge/files/);
iter14c added diagnostic logging that confirmed T4 was firing on track
change but permissive CTs still showed the cached first-track metadata.

iter15: state-tracked CHANGED notifications. extended_T2 saves the
RegisterNotification transId; T4 detects track_id changes against a
state file and emits a CHANGED with the saved transId before replying
to GetElementAttributes. INTERIM/CHANGED both carried the file's real
track_id. Hardware-tested 2026-05-06 — DEADLOCKED permissive CTs: returning a
real track_id flips permissive CTs into "stable identity, only refresh on
CHANGED" mode; T4 fires only when permissive CTs poll; permissive CTs won't poll until
it sees a CHANGED. 14 minutes of zero AVRCP traffic confirmed.

iter16: same architecture as iter15 but INTERIM/CHANGED's track_id
field is hardcoded to the 0xFF×8 sentinel ("not bound to a particular
media element" per AVRCP 1.4 §6.7.2). State file's bytes 0..7 still
hold the file's last-synced track_id — that's what T4 compares against
to know when to emit CHANGED. Restores iter14c-style polling
behaviour and adds CHANGED edges on real track changes so permissive CTs
invalidates its 0xFF×8-keyed cache and re-renders.

iter17a/b: proactive CHANGED via T5 + Java-side cardinality bypass +
single-frame multi-attribute response fix (T4 calling convention).

iter19a (Phase A0 of docs/AVRCP13-COMPLIANCE-PLAN.md): adds T_charset
and T_battery for PDUs 0x17 and 0x18 (CT→TG informational pair, both
spec-mandated, both rejected pre-iter19a → caused strict CTs failure per
the strict-CT iter18d capture), and fixes the existing T2/T5 wire shape
for TRACK_CHANGED notifications (was passing r1=transId which hits the
response builder's reject-shape path; now r1=0 for spec-correct
emission of reasonCode + event_id + track_id). Compliance scorecard
goes 3→5 mandatory PDUs handled and 2→5 spec-correct.

Usage:
    python3 patch_libextavrcp_jni.py libextavrcp_jni.so
    python3 patch_libextavrcp_jni.py libextavrcp_jni.so --output /tmp/jni.patched
    python3 patch_libextavrcp_jni.py libextavrcp_jni.so --verify-only
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

# Allow `from _trampolines import ...` when invoked from any cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _trampolines import build as build_trampolines, T4_VADDR
from _thumb2asm import _encode_t4_branch  # noqa: F401 (used to build T2 stub)
from _thumb2asm import Asm

# iter17a: notificationTrackChangedNative lives at vaddr 0x3bc0 in
# libextavrcp_jni.so. Java's BTAvrcpMusicAdapter.handleKeyMessage (with the
# cardinality if-eqz NOPed by patch_mtkbt_odex.py's iter17a entry) calls this
# native on every track-change broadcast from Y1MediaBridge. We replace its
# entry instruction with `b.w T5` so it lands in our state-aware trampoline.
NATIVE_TRACK_CHANGED_VADDR = 0x3bc0

# iter22b: notificationPlayStatusChangedNative at vaddr 0x3c88. Same shape as
# the TRACK_CHANGED hook above, paired with the iter22b cardinality NOP at
# 0x3c4fe in MtkBt.odex (sswitch_18a / event 0x01 case in handleKeyMessage's
# nested sparse-switch). Replace entry instruction with `b.w T9` so every
# Y1MediaBridge `playstatechanged` broadcast lands in T9, which fires
# PLAYBACK_STATUS_CHANGED CHANGED via PLT_reg_notievent_playback_rsp on edge.
# Closes the AVRCP §6.7.1 spec gap left by iter20b's INTERIM-only T8.
NATIVE_PLAY_STATUS_CHANGED_VADDR = 0x3c88

STOCK_MD5  = "fd2ce74db9389980b55bccf3d8f15660"
OUTPUT_MD5 = "e2790518d258e87326c8a65ad7b8f5c8"  # iter22d — T6 live position extrapolation via clock_gettime(CLOCK_BOOTTIME)

# ---------------------------------------------------------------- T1

# T1 — GetCapabilities trampoline at 0x7308 (overwrites testparmnum, 40 of 48
# bytes). Advertises every event we actually handle in the trampoline chain:
#   0x01 PLAYBACK_STATUS_CHANGED       (T8, iter20b)
#   0x02 TRACK_CHANGED                 (extended_T2 INTERIM + T4/T5 CHANGED)
#   0x03 TRACK_REACHED_END             (T8, iter20b — INTERIM only)
#   0x04 TRACK_REACHED_START           (T8, iter20b — INTERIM only)
#   0x05 PLAYBACK_POS_CHANGED          (T8, iter20b — INTERIM only)
#   0x06 BATT_STATUS_CHANGED           (T8, iter20b — INTERIM only, canned)
#   0x07 SYSTEM_STATUS_CHANGED         (T8, iter20b — INTERIM only, canned)
# Per the spec-compliance rule (feedback_avrcp_spec_compliance.md), advertise
# only what we actually implement. 0x08 PLAYER_APPLICATION_SETTING_CHANGED is
# Phase C territory (PlayerApplicationSettings PDUs 0x11–0x16) and stays
# unadvertised until we ship that. Pre-iter20b the array was just `02` count=1
# for historical reasons (permissive CTs-era exploration; iter10 reduced from 5 events
# to 1 because permissive CTs abandoned the registration sequence on the first
# NOT_IMPLEMENTED). Now that we actually handle the events we advertise,
# strict CTs that gate on coverage will subscribe to all 7.
T1_TRAMPOLINE = bytes([
    0x9D, 0xF8, 0x7E, 0x01,                  # ldrb.w r0, [sp, #382]
    0x10, 0x28,                               # cmp r0, #0x10
    0x0D, 0xD1,                               # bne.n 0x732c (bridge to T2)
    0x04, 0xA3,                               # adr r3, 0x7324
    0x05, 0xF1, 0x08, 0x00,                  # add.w r0, r5, #8
    0x00, 0x21,                               # movs r1, #0
    0x07, 0x22,                               # movs r2, #7   (events count = 7, iter20b)
    0xFC, 0xF7, 0x60, 0xE9,                  # blx 0x35dc (PLT: get_capabilities_rsp)
    0xFF, 0xF7, 0x04, 0xBF,                  # b.w 0x712a (epilogue)
    0x00, 0xBF,                               # nop
    0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x00,  # iter20b — events 0x01..0x07
    0xFF, 0xF7, 0xD2, 0xBF,                  # b.w 0x72d4 (T2 stub)
])
assert len(T1_TRAMPOLINE) == 40

# Stock testparmnum first 40 bytes.
TESTPARMNUM_STOCK = bytes([
    0x10, 0xB5, 0x04, 0x20, 0x07, 0x4C, 0x08, 0x4A,
    0x7C, 0x44, 0x21, 0x46, 0x7A, 0x44, 0xFB, 0xF7,
    0xF4, 0xEF, 0x06, 0x4A, 0x04, 0x20, 0x21, 0x46,
    0x00, 0x23, 0x7A, 0x44, 0xFB, 0xF7, 0xEC, 0xEF,
    0x00, 0x20, 0x10, 0xBD, 0x01, 0x07, 0x00, 0x00,
])
assert len(TESTPARMNUM_STOCK) == 40

# ---------------------------------------------------------------- T2 stub

# Stock classInitNative (48 bytes) — entry + body + literal pool.
CLASSINITNATIVE_STOCK = bytes([
    0x10, 0xB5, 0x04, 0x20, 0x07, 0x4C, 0x08, 0x4A,
    0x7C, 0x44, 0x21, 0x46, 0x7A, 0x44, 0xFC, 0xF7,
    0x10, 0xE8, 0x06, 0x4A, 0x04, 0x20, 0x21, 0x46,
    0x00, 0x23, 0x7A, 0x44, 0xFC, 0xF7, 0x08, 0xE8,
    0x00, 0x20, 0x10, 0xBD, 0x39, 0x07, 0x00, 0x00,
    0xCA, 0x12, 0x00, 0x00, 0xDD, 0x2C, 0x00, 0x00,
])
assert len(CLASSINITNATIVE_STOCK) == 48


def _t2_stub(extended_t2_vaddr: int) -> bytes:
    """Build the 48-byte block at 0x72d0:
        0x72d0: movs r0, #0; bx lr      (classInitNative `return 0` stub)
        0x72d4: b.w extended_T2         (the only T2 logic; everything else
                                         is dispatched inside extended_T2)
        0x72d8..0x72ff: zero filler (unreachable)
    """
    a = Asm(0x72d0)
    a.raw(bytes([0x00, 0x20, 0x70, 0x47]))   # movs r0, #0; bx lr
    a.labels["target"] = extended_t2_vaddr
    a.b_w("target")
    while len(a.buf) < 48:
        a.buf.append(0x00)
    return a.resolve()


# ---------------------------------------------------------------- LOAD #1 phdr

LOAD1_PHDR_OFFSET = 0x54
LOAD1_FILESZ_OFFSET = LOAD1_PHDR_OFFSET + 16
LOAD1_MEMSZ_OFFSET  = LOAD1_PHDR_OFFSET + 20
LOAD1_OLD_SIZE = 0xac54

# ---------------------------------------------------------------- patch list builder


def _native_track_changed_stub(t5_vaddr: int) -> bytes:
    """iter17a: replace the first 4 bytes of notificationTrackChangedNative
    with `b.w T5`. The remaining 196 bytes of the original function body are
    unreachable but left in place (they form valid but dead code; harmless)."""
    a = Asm(NATIVE_TRACK_CHANGED_VADDR)
    a.labels["target"] = t5_vaddr
    a.b_w("target")
    return a.resolve()


def _native_play_status_changed_stub(t9_vaddr: int) -> bytes:
    """iter22b: replace the first 4 bytes of notificationPlayStatusChangedNative
    with `b.w T9`. The remaining bytes of the original function body are
    unreachable but left in place (valid dead code; harmless)."""
    a = Asm(NATIVE_PLAY_STATUS_CHANGED_VADDR)
    a.labels["target"] = t9_vaddr
    a.b_w("target")
    return a.resolve()


# Stock first 4 bytes of notificationTrackChangedNative — the prologue's
# `stmdb sp!, {r4, r5, r6, r7, r8, r9, sl, lr}` instruction.
NATIVE_TRACK_CHANGED_STOCK_PROLOGUE = bytes([0x2D, 0xE9, 0xF0, 0x47])

# Stock first 4 bytes of notificationPlayStatusChangedNative.
# Disassembled: stmdb sp!, {r0, r1, r4, r5, r6, r7, r8, lr} (reg list 0x41F3) --
# distinct from notificationTrackChangedNative's prologue (0x47F0) because the
# play_status native takes 3 jbyte args (Java arg3 = play_status arrives in r4
# per the AAPCS register/stack split for variadic-byte Java natives), and the
# stock body needs r0/r1 (env, this) preserved for re-use after the call into
# the AVRCP service.  We don't care about the original body — overwriting the
# first 4 bytes with `b.w T9` short-circuits everything past it.
NATIVE_PLAY_STATUS_CHANGED_STOCK_PROLOGUE = bytes([0x2D, 0xE9, 0xF3, 0x41])


def build_patches() -> tuple[list[dict], int]:
    """Build the patch list. Returns (patches, new_load1_size)."""
    blob, addrs = build_trampolines()
    extended_t2_vaddr = addrs["extended_T2"]
    t5_vaddr = addrs["T5"]
    t9_vaddr = addrs["T9"]
    new_load1_size = T4_VADDR + len(blob)

    patches = [
        {
            "name": "R1: redirect bne.n 0x65bc → bl.w 0x7308 (T1) at 0x6538",
            "offset": 0x6538,
            "before": bytes([0x40, 0xD1, 0x09, 0x25]),  # bne.n 0x65bc; movs r5, #9
            "after":  bytes([0x00, 0xF0, 0xE6, 0xFE]),  # bl.w 0x7308
        },
        {
            "name": "T1: GetCapabilities trampoline (testparmnum) at 0x7308",
            "offset": 0x7308,
            "before": TESTPARMNUM_STOCK,
            "after":  T1_TRAMPOLINE,
        },
        {
            "name": (
                f"iter17a: notificationTrackChangedNative @ 0x{NATIVE_TRACK_CHANGED_VADDR:x}"
                f" → b.w T5 (0x{t5_vaddr:x}) — proactive CHANGED on track change"
            ),
            "offset": NATIVE_TRACK_CHANGED_VADDR,
            "before": NATIVE_TRACK_CHANGED_STOCK_PROLOGUE,
            "after":  _native_track_changed_stub(t5_vaddr),
        },
        {
            "name": (
                f"iter22b: notificationPlayStatusChangedNative @"
                f" 0x{NATIVE_PLAY_STATUS_CHANGED_VADDR:x} → b.w T9 (0x{t9_vaddr:x})"
                f" — proactive PLAYBACK_STATUS_CHANGED on play/pause edge"
            ),
            "offset": NATIVE_PLAY_STATUS_CHANGED_VADDR,
            "before": NATIVE_PLAY_STATUS_CHANGED_STOCK_PROLOGUE,
            "after":  _native_play_status_changed_stub(t9_vaddr),
        },
        {
            "name": (
                f"T2 stub: classInitNative stub + b.w 0x{extended_t2_vaddr:x}"
                " (extended_T2) at 0x72d0"
            ),
            "offset": 0x72d0,
            "before": CLASSINITNATIVE_STOCK,
            "after":  _t2_stub(extended_t2_vaddr),
        },
        {
            "name": (
                f"trampoline blob @ 0x{T4_VADDR:x} ({len(blob)} bytes "
                f"in LOAD #1 padding; final vaddr 0x{new_load1_size:x})"
            ),
            "offset": T4_VADDR,
            "before": bytes([0x00] * len(blob)),  # stock LOAD #1 padding is zeros
            "after":  blob,
        },
        {
            "name": f"LOAD #1 filesz: 0x{LOAD1_OLD_SIZE:x} → 0x{new_load1_size:x}",
            "offset": LOAD1_FILESZ_OFFSET,
            "before": LOAD1_OLD_SIZE.to_bytes(4, "little"),
            "after":  new_load1_size.to_bytes(4, "little"),
        },
        {
            "name": f"LOAD #1 memsz: 0x{LOAD1_OLD_SIZE:x} → 0x{new_load1_size:x}",
            "offset": LOAD1_MEMSZ_OFFSET,
            "before": LOAD1_OLD_SIZE.to_bytes(4, "little"),
            "after":  new_load1_size.to_bytes(4, "little"),
        },
    ]
    return patches, new_load1_size


# ---------------------------------------------------------------- I/O helpers


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def verify(data: bytes, mode: str, patches: list[dict]) -> tuple[bool, list[dict]]:
    results = []
    for p in patches:
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
        fmt = lambda b: b.hex(" ") if n <= 8 else b[:8].hex(" ") + " ..."
        print(f"  [{'OK' if r['ok'] else 'FAIL'}] 0x{r['offset']:06x}  {r['name']}")
        if not r["ok"]:
            print(f"          expected ({mode}): {fmt(r[mode])}")
            print(f"          actual:            {fmt(r['actual'])}")
    print("-" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Minimum JNI patch — route size-9 msg-519 frames to BT-SIG VENDOR path"
    )
    parser.add_argument("input", help="Path to stock libextavrcp_jni.so")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path (default: output/libextavrcp_jni.so.patched)")
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
    # so when the input already hashes to OUTPUT_MD5 there's nothing to do.
    if OUTPUT_MD5 is not None and input_md5 == OUTPUT_MD5:
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
        if OUTPUT_MD5 is not None:
            print(f"       Expected stock ({STOCK_MD5}) or already-patched ({OUTPUT_MD5}).")
        print("       Use --skip-md5 for alternate stock builds.")
        sys.exit(1)

    patches, new_load1_size = build_patches()

    # Site-level verification is only informative when MD5 alone isn't
    # sufficient: alternate stock build (--skip-md5) or development mode
    # where OUTPUT_MD5 isn't pinned yet. On the normal happy path the
    # input-MD5 and output-MD5 checks already cover every byte in the file.
    show_sites = args.skip_md5 or OUTPUT_MD5 is None

    if show_sites:
        pre_ok, pre_results = verify(data, "before", patches)
        print_results("Pre-patch verification (stock)", pre_results, "before")

        if not pre_ok:
            post_ok, post_results = verify(data, "after", patches)
            print_results("Already-patched check", post_results, "after")
            if post_ok:
                print("\nBinary is already patched. Nothing to do.")
                sys.exit(0)
            print("\nERROR: patch site matches neither stock nor patched.")
            sys.exit(1)

    if args.verify_only:
        print("\nVerify-only — no output written.")
        sys.exit(0)

    for p in patches:
        data[p["offset"]: p["offset"] + len(p["after"])] = p["after"]

    output_md5 = md5(data)
    output_md5_mismatch = OUTPUT_MD5 is not None and output_md5 != OUTPUT_MD5

    # Post-patch site verification fires either when we're already in a
    # site-aware mode (developer / alternate stock) or as a diagnostic when
    # the produced output doesn't hash to the pinned expected value.
    if show_sites or output_md5_mismatch:
        post_ok, post_results = verify(data, "after", patches)
        print_results("Post-patch verification", post_results, "after")
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

    if OUTPUT_MD5 is None:
        out_tag = f"[set OUTPUT_MD5 = \"{output_md5}\"]"
    elif output_md5 == OUTPUT_MD5:
        out_tag = "[OK — matches expected]"
    else:
        out_tag = f"[MISMATCH — expected {OUTPUT_MD5}]"

    print(f"\nOutput: {output_path}  ({len(data):,} bytes)")
    print(f"MD5:    {output_md5}  {out_tag}")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/lib/libextavrcp_jni.so")
    print(f"  adb shell chmod 644 /system/lib/libextavrcp_jni.so")
    print(f"  adb reboot")
    print(f"  logcat | grep -E 'CMD_FRAME_IND|registerNotificationInd|cardinality|Y1MediaBridge.IBTAvrcpMusic'")

    if output_md5_mismatch and not args.skip_md5:
        print("\nERROR: output MD5 doesn't match expected. Output was written but"
              " the patcher's expected hash is stale or the patch logic diverged."
              " Pass --skip-md5 to suppress.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
