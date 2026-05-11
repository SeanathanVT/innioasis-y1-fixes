#!/usr/bin/env python3
"""
patch_mtkbt_odex.py — Patch stock MtkBt.odex -> MtkBt.odex.patched

Stock binary md5:  11566bc23001e78de64b5db355238175
Output md5:        00cc642742044286966cbb7b01135ca7

ODEX structure:
  ODEX header (0x28 bytes): magic "dey\n036\0", dex_offset=0x28, dex_length=0x98490
  DEX data at 0x28: standard DEX with magic "dex\n035\0"
  DEX adler32 at ODEX file offset 0x30 (= DEX header field offset 0x08)
    covers DEX bytes [12 : dex_length]  (ODEX file bytes [0x34 : 0x984b8])

--- Patch 1: getPreferVersion() return value ---

  BTAvrcpProfile.getPreferVersion() (code @ DEX 0x0003e0b0):
    const/16 v0, #10   ; DEX bytes: 13 00 0A 00
    return v0

  This return value drives the Java-side dispatcher's command-handling
  cascade. The stock value 10 routes commands through MtkBt's compiled-in
  AVRCP 1.0 handlers and rejects anything past PASSTHROUGH. Returning 14
  unblocks 1.3+ command dispatch through the rest of the JNI / native path.
  This is internal flag bookkeeping inside MtkBt's BlueAngel layer; the
  on-the-wire AVRCP version is determined by the SDP record (V1 patch in
  patch_mtkbt.py), not by this value.

    getPreferVersion() -> checkCapability() -> activateConfig_3req(bitmask)
    -> g_tg_feature = 0x0e (@ libextavrcp_jni.so 0xD29C)
    -> activate_1req reads global -> btmtk_avrcp_send_activate_req payload[6] = 0x0e
    -> mtkbt daemon receives the unblocked internal code on abstract socket bt.ext.adp.avrcp

  Patch: change return value from 10 (0x0a) to 14 (0x0e).
  Confirmed working in logcat: getPreferVersion:14, _activate_1req
  version:14 sdpfeature:35.

--- Patch 3: cardinality bypass for proactive TRACK_CHANGED ---

  In BTAvrcpMusicAdapter.handleKeyMessage(Message)V, the sparse-switch case
  for msg.what:34 (ACTION_REG_NOTIFY) → arg1=2 (TRACK_CHANGED) goes:

      iget-object v5, this, mRegisteredEvents:BitSet
      invoke-virtual v5, vtable@20    ; bitset.get(2) — peer subscription bit
      move-result v5
      if-eqz v5, :cond_184            ; <<< if no peer subscribed, skip
      ; ... build sMusicId, log "songid:N", call notificationTrackChangedNative

  The `if-eqz` is at ODEX file offset 0x03c530, encoded as `38 05 da ff` (4 B).
  The Java-side BitSet is never populated because the JNI's TG layer doesn't
  use the Java cardinality bookkeeping (it has its own native conn-state
  tracking). So Java's view is permanently `cardinality:0` and the
  notification path always exits early.

  This patch NOPs out the if-eqz (4 bytes → 4 zero bytes = two `nop` opcodes).
  Java now always invokes notificationTrackChangedNative when the music app
  fires a `com.android.music.metachanged` broadcast. The libextavrcp_jni.so
  side redirects that native to a state-aware T5 trampoline that emits
  track_changed_rsp CHANGED via the same PLT entry our T4 uses.

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
import os
import struct
import sys
import zlib
from pathlib import Path

STOCK_MD5         = "11566bc23001e78de64b5db355238175"
OUTPUT_MD5        = "00cc642742044286966cbb7b01135ca7"

# Build-time debug toggle. `apply.bash --debug` exports KOENSAYR_DEBUG=1.
# Placeholder — currently we only patch DEX bytecode in-place via byte
# offsets, with no smali decoding. If we ever need to add Log.d to the
# AVRCP Java dispatcher (BTAvrcpMusicAdapter.handleKeyMessage etc.), we
# can deodex first, gate on this flag, and inject before re-odexing. Once
# the debug build diverges from release, pin a separate hash here.
DEBUG_LOGGING     = os.environ.get("KOENSAYR_DEBUG", "") == "1"
OUTPUT_DEBUG_MD5  = OUTPUT_MD5

# Effective expected output MD5 for the current invocation.
EXPECTED_OUTPUT_MD5 = OUTPUT_DEBUG_MD5 if DEBUG_LOGGING else OUTPUT_MD5

DEX_OFFSET     = 0x28
ADLER_FILE_OFF = 0x30

PATCHES = [
    {
        "name":   "[F1] getPreferVersion return value (BlueAngel internal 10 -> 14, unblocks 1.3+ dispatch)",
        "offset": 0x3e0ea,
        "before": bytes([0x0a]),
        "after":  bytes([0x0e]),
    },
    {
        "name":   "handleKeyMessage TRACK_CHANGED cardinality bypass (NOP if-eqz at 0x3c530)",
        "offset": 0x3c530,
        "before": bytes([0x38, 0x05, 0xda, 0xff]),  # if-eqz v5, +-38 (-> :cond_184)
        "after":  bytes([0x00, 0x00, 0x00, 0x00]),  # nop; nop
    },
    {
        "name":   "handleKeyMessage PLAYBACK_STATUS_CHANGED cardinality bypass (NOP if-eqz at 0x3c4fe)",
        # sswitch_18a / event 0x01 case in handleKeyMessage's nested
        # sparse-switch (same idiom as the TRACK_CHANGED NOP above).
        # Without this NOP the JNI's notificationPlayStatusChangedNative is
        # never invoked because the Java BitSet of registered events is
        # permanently empty (TG bookkeeping isn't updated by our
        # trampolines). With the NOP, the native fires on every
        # `com.android.music.playstatechanged` broadcast and lands in T9 via
        # the libextavrcp_jni.so hook at 0x3c88.
        "offset": 0x3c4fe,
        "before": bytes([0x38, 0x05, 0xf3, 0xff]),  # if-eqz v5, +-13 (-> :cond_184)
        "after":  bytes([0x00, 0x00, 0x00, 0x00]),  # nop; nop
    },
    {
        "name":   "BTAvrcpMusicAdapter$3.onReceive playstatechanged dedupe NOP",
        # NOP `if-eq v3, v2, :cond_50` (mPreviousPlayStatus dedupe) so every
        # playstatechanged broadcast posts msg=1 + msg=2. T5 / T9 internal
        # dedupes gate wire emits on actual edges; the broadcast-handler
        # dedupe was blocking the 1 Hz position tick, the papp CHANGED
        # loop, and PAUSED → STOPPED transitions.
        "offset": 0x3b310,
        "before": bytes([0x32, 0x23, 0xea, 0xff]),  # if-eq v3, v2, +0xffea
        "after":  bytes([0x00, 0x00, 0x00, 0x00]),  # nop; nop
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
        description="Patch stock MtkBt.odex for AVRCP 1.3+ Java-dispatcher unblock + BT toggle fix"
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

    if data[:4] != b"dey\n":
        print("ERROR: not an ODEX file (missing 'dey\\n' magic)")
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
                print("\nBinary is already fully patched. Nothing to do.")
                sys.exit(0)
            print("\nERROR: patch sites match neither stock nor fully-patched state.")
            sys.exit(1)

        # adler32 check on input — only meaningful in site-aware mode (mismatch
        # there could indicate the alternate-stock build has a different DEX
        # body than expected). On the normal happy path the input-MD5 already
        # validated the entire file, including the adler32 field.
        stored_adler   = struct.unpack_from("<I", data, ADLER_FILE_OFF)[0]
        computed_adler = compute_adler32(data)
        if stored_adler != computed_adler:
            print(f"\n  [WARN] 0x{ADLER_FILE_OFF:06x}  "
                  f"adler32 stored=0x{stored_adler:08x} computed=0x{computed_adler:08x}")
            print("  WARNING: adler32 mismatch on input — continuing anyway")

    if args.verify_only:
        print("\nVerify-only — no output written.")
        sys.exit(0)

    for p in PATCHES:
        data[p["offset"]: p["offset"] + len(p["after"])] = p["after"]

    # adler32 must always be recomputed and written back regardless of
    # verification mode — Dalvik refuses to load the DEX without it.
    new_adler = compute_adler32(data)
    struct.pack_into("<I", data, ADLER_FILE_OFF, new_adler)

    output_md5 = md5(data)
    output_md5_mismatch = EXPECTED_OUTPUT_MD5 is not None and output_md5 != EXPECTED_OUTPUT_MD5

    # Post-patch site verification fires either when we're already in a
    # site-aware mode (developer / alternate stock) or as a diagnostic when
    # the produced output doesn't hash to the pinned expected value.
    if show_sites or output_md5_mismatch:
        post_ok, post_results = verify(data, "after")
        print_results("Post-patch verification", post_results, "after")

        stored_after   = struct.unpack_from("<I", data, ADLER_FILE_OFF)[0]
        computed_after = compute_adler32(data)
        if stored_after != computed_after:
            print(f"  [FAIL] 0x{ADLER_FILE_OFF:06x}  "
                  f"adler32 stored=0x{stored_after:08x} computed=0x{computed_after:08x}")
            post_ok = False

        if not post_ok:
            print("\nERROR: post-patch verification failed — output not written.")
            sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / "MtkBt.odex.patched"

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
