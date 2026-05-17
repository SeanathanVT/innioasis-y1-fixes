#!/usr/bin/env python3
"""
avrcp-wire-trace.py — reconstruct outbound AVRCP wire-frame shape from
the trampoline-side `Y1T :` debug logs emitted by `apply.bash --debug`.

The trampolines in `src/patches/_trampolines.py` (DEBUG_NATIVE_LOG
branch) call `__android_log_print(INFO, "Y1T", fmt, value)` at every
wire-side emit site. With KOENSAYR_DEBUG=1 in the build, those log
lines surface in `adb logcat -s Y1T:*`:

    Y1T : T4a=00010014        attr_id=0x01 (Title), strlen=20
    Y1T : T4a=00020017        attr_id=0x02 (Artist), strlen=23
    Y1T : T4a=00030014        attr_id=0x03 (Album), strlen=20
    Y1T : T4a=00040001        attr_id=0x04 (MediaNum), strlen=1
    Y1T : T4a=00050002        attr_id=0x05 (TotalNum), strlen=2
    Y1T : T4a=00060000        attr_id=0x06 (Genre), strlen=0
    Y1T : T4a=00070006        attr_id=0x07 (PlayTime), strlen=6
    Y1T : T8reg ev=01         RegisterNotification(PlayStatus)
    Y1T : T5emit aid=00000001 TrackChanged Identifier high32=1
    Y1T : T9emit pstat=1      PlaybackStatusChanged CHANGED PlayStatus=1
    Y1T : T9emit pos=10077    PlaybackPosChanged CHANGED pos_ms=10077

This script:
  - Groups consecutive T4a=... lines into a single GEA response.
  - Computes the wire-frame size from the per-attribute strlens.
  - Flags responses that exceed the 502-byte L2CAP MTU threshold
    (where mtkbt's fcn.0xed50 sets AVCTP packet_type=1 START and
    triggers fragmentation).
  - Surfaces T8reg / T5emit / T9emit lines verbatim for cross-correlation.

Usage:
    adb logcat -s Y1T:* > bolt.log
    ./avrcp-wire-trace.py bolt.log
    ./avrcp-wire-trace.py bolt.log --gea-only       # only GEA wire-size summary
    ./avrcp-wire-trace.py bolt.log --frag-only      # only responses > 502 B

Notes:
  - Logs only show what the TRAMPOLINES emit. What mtkbt does on the
    wire (packet_type, fragment ordering) isn't directly visible. If
    wire_size > 502, mtkbt is *expected* to fragment; this tool flags
    those responses. Whether the fragmentation actually reaches the CT
    correctly requires an external sniffer or btmon (unavailable on Y1).
  - L2CAP MTU on the AVRCP CONTROL channel is 512 with 10 bytes of
    AVCTP/AVRCP header overhead = 502 bytes of AVRCP payload per single
    AVCTP packet. See INVESTIGATION.md Trace #48 for the derivation.
"""

import argparse
import re
import sys
from collections import defaultdict


# Wire size formula (per AVRCP 1.3 §6.6.1 Tbl 6.26 + §6.4 + §6.3):
#   AVCTP header (3)              -- transactionID + packet_type/CR/IPID + ProfileID
#   AV/C outer (3)                -- ctype + subunit_type/id + opcode
#   companyID (3)                 -- BT SIG 00 19 58
#   PDU_ID (1)                    -- 0x20 GetElementAttributes
#   reserved (1)                  -- 0x00
#   parameter_length (2)          -- 16-bit BE length of the rest
#   num_attribs (1)               -- count of attribute entries that follow
#   N × attribute_entry:
#       attr_id (4)               -- BE u32
#       char_set (2)              -- BE u16, 0x006A = UTF-8
#       length (2)                -- BE u16
#       value (variable, length bytes)
# Constant overhead = 13 + 1 (num_attribs) = 14 bytes BEFORE the first attribute.
# Per-attribute overhead = 8 bytes header.
# AVCTP packet_type=0 (Single) holds up to MTU - 3 (AVCTP) = 509 bytes of payload.
# Headers above are the *payload* — so the AVRCP-side budget within a single
# AVCTP packet is 509 - 14 = 495 bytes of (per-attribute 8 + strlen).

L2CAP_MTU = 512
AVCTP_HEADER_LEN = 3            # transactionID + ProfileID
GEA_FIXED_OVERHEAD = 14         # AV/C outer + companyID + PDU + paramlen + num_attribs
FRAG_THRESHOLD = L2CAP_MTU - AVCTP_HEADER_LEN - GEA_FIXED_OVERHEAD - 7   # = 488

ATTR_NAMES = {
    0x01: "Title",
    0x02: "Artist",
    0x03: "Album",
    0x04: "MediaNum",
    0x05: "TotalNum",
    0x06: "Genre",
    0x07: "PlayTime",
}

# Match `Y1T : <text>` lines from `adb logcat -s Y1T:*` output. Tolerant
# of intermediate whitespace, PID columns, etc.
LINE_RE = re.compile(r"Y1T\s*:\s*(?P<text>\S.*)$")
T4_RE = re.compile(r"T4a=([0-9a-fA-F]{8})")
T8_RE = re.compile(r"T8reg\s+ev=([0-9a-fA-F]+)")
T5_RE = re.compile(r"T5emit\s+aid=([0-9a-fA-F]+)")
T9PSTAT_RE = re.compile(r"T9emit\s+pstat=(\d+)")
T9POS_RE = re.compile(r"T9emit\s+pos=(\d+)")


def parse_lines(path):
    """Yield (timestamp_str, text) for each Y1T log line in adb logcat output."""
    with open(path) as f:
        for line in f:
            m = LINE_RE.search(line)
            if not m:
                continue
            # Try to extract a timestamp prefix (logcat default format).
            # Falls back to None if absent.
            ts_match = re.match(r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)", line)
            ts = ts_match.group(1) if ts_match else ""
            yield ts, m.group("text").rstrip()


def summarize_gea(attrs, start_ts):
    """Compute wire size + per-attribute breakdown for one GEA response."""
    total_strlen = sum(strlen for _, strlen in attrs)
    n = len(attrs)
    wire_size = GEA_FIXED_OVERHEAD + n * 8 + total_strlen
    avctp_payload = wire_size + AVCTP_HEADER_LEN  # what mtkbt hands to L2CAP

    fragments = (avctp_payload + L2CAP_MTU - 1 - AVCTP_HEADER_LEN) // (L2CAP_MTU - AVCTP_HEADER_LEN)
    will_fragment = avctp_payload > L2CAP_MTU

    return {
        "ts": start_ts,
        "n": n,
        "total_strlen": total_strlen,
        "wire_size": wire_size,
        "avctp_payload": avctp_payload,
        "fragments": fragments,
        "will_fragment": will_fragment,
        "attrs": attrs,
    }


def format_gea(s, verbose=False):
    out = [
        f"[{s['ts']}] GEA response: N={s['n']} total_strlen={s['total_strlen']}"
        f" wire={s['wire_size']}B AVCTP_payload={s['avctp_payload']}B"
        f" → fragments={s['fragments']}{' (PACKET_TYPE=START)' if s['will_fragment'] else ''}"
    ]
    if verbose:
        for attr_id, strlen in s["attrs"]:
            name = ATTR_NAMES.get(attr_id, "?")
            out.append(f"     attr={attr_id:#04x} ({name:<8})  len={strlen}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile", help="adb logcat output (text)")
    ap.add_argument("--gea-only", action="store_true",
                    help="emit only GEA wire-size summaries (default also includes T8/T5/T9)")
    ap.add_argument("--frag-only", action="store_true",
                    help="emit only GEA responses that exceed the 502-B fragmentation threshold")
    ap.add_argument("--no-attr-breakdown", action="store_true",
                    help="omit per-attribute strlen rows from GEA summaries")
    args = ap.parse_args()

    summaries = []
    current_gea = []
    current_gea_start_ts = ""
    other_lines = []

    def flush_gea():
        nonlocal current_gea, current_gea_start_ts
        if current_gea:
            summaries.append(summarize_gea(current_gea, current_gea_start_ts))
            current_gea = []
            current_gea_start_ts = ""

    for ts, text in parse_lines(args.logfile):
        m4 = T4_RE.search(text)
        if m4:
            packed = int(m4.group(1), 16)
            attr_id = (packed >> 16) & 0xFFFF
            strlen = packed & 0xFFFF
            if not current_gea:
                current_gea_start_ts = ts
            current_gea.append((attr_id, strlen))
            continue

        # Any non-T4 line closes the current GEA response.
        if current_gea:
            # An attr_id of 0 in our T4_RE pack only happens at idx=0 if
            # attr_id wasn't shifted in — defensive check, shouldn't occur.
            flush_gea()

        other_lines.append((ts, text))

    flush_gea()

    # Output
    if not args.gea_only:
        for ts, text in other_lines:
            print(f"[{ts}] {text}")

    for s in summaries:
        if args.frag_only and not s["will_fragment"]:
            continue
        print(format_gea(s, verbose=not args.no_attr_breakdown))

    # Footer summary
    n_gea = len(summaries)
    n_frag = sum(1 for s in summaries if s["will_fragment"])
    print(f"\n# {n_gea} GEA response(s) parsed; {n_frag} would fragment (wire_size > {FRAG_THRESHOLD} B)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
