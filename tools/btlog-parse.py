#!/usr/bin/env python3
"""
btlog-parse.py — decode the structured stream from mtkbt's @btlog socket.

Frame format (reverse-engineered from a captured stream — see Trace #9 in INVESTIGATION.md):

  off  size  field
  ---  ----  -----
   0    1    0x55 marker ('U')
   1    1    0x00 pad
   2    1    L = body length (count of bytes from offset 7 onward)
   3    2    seq (2 ASCII chars; alphabetical, increments per frame)
   5    1    sev (category/severity; 0x12 early, 0xb4 later)
   6    1    0x00 pad
   7   12    body header:
              [0:2]   constant-ish (often 00 e5)
              [2:6]   timestamp (u32 LE) — monotonic per process lifetime
              [6:10]  zero/flag (often 00 00 00 00)
              [10:12] u16 LE — appears to be format-string ID/length
   19  ...   text payload (NUL-terminated, possibly with trailing \\r\\n)

Usage:
  ./btlog-parse.py <input.bin>                         # decode → stdout (one line per frame)
  ./btlog-parse.py <input.bin> --tag-include AVRCP     # only frames whose text contains "AVRCP"
  ./btlog-parse.py <input.bin> --tag-exclude '[BT]'    # drop the byte-level HCI noise
  ./btlog-parse.py <input.bin> --raw                   # print framing meta too
"""

import sys
import argparse
import struct

PRELUDE = b"connected to @btlog, dumping..."

def parse_frames(data):
    # Skip the prelude line that btlog-dump itself prints to stdout.
    i = 0
    p = data.find(PRELUDE)
    if p >= 0:
        # advance to the byte after the trailing \n
        i = p + len(PRELUDE)
        while i < len(data) and data[i] in (0x0d, 0x0a):
            i += 1
    while i < len(data) - 8:
        if data[i] != 0x55 or data[i + 1] != 0x00:
            i += 1
            continue
        L = data[i + 2]
        if L < 12 or i + 7 + L > len(data):
            # Frame too short or runs off the end → resync
            i += 1
            continue
        seq = data[i + 3:i + 5]
        sev = data[i + 5]
        body = data[i + 7:i + 7 + L]
        if len(body) < 12:
            i += 1
            continue
        ts = struct.unpack_from('<I', body, 2)[0]
        flags = struct.unpack_from('<I', body, 6)[0]
        msgid = struct.unpack_from('<H', body, 10)[0]
        # Body has a variable-length sub-header before the text — typically
        # 10-13 bytes including timestamp, flags, format-id, and sometimes
        # a leading separator like ".@" or ".@[". Practical heuristic that
        # works across both 0x12 (xlog text) and 0xb4 (HCI snoop binary)
        # severities: scan body for the first run of >=4 printable ASCII
        # chars, take the run from there until the first NUL or the next
        # 0x55 frame marker, whichever comes first.
        text = ''
        for start in range(len(body)):
            if 32 <= body[start] < 127 and start + 4 <= len(body) and \
               all(32 <= b < 127 or b in (0x09, 0x0a, 0x0d) for b in body[start:start+4]):
                end = start
                while end < len(body):
                    b = body[end]
                    # stop at NUL, non-printable, or next frame marker
                    if b == 0x00: break
                    if b == 0x55 and end + 1 < len(body) and body[end+1] == 0x00: break
                    if not (32 <= b < 127 or b in (0x09, 0x0a, 0x0d)): break
                    end += 1
                text = body[start:end].rstrip(b'\r\n ').decode('latin-1', errors='replace')
                break
        yield {
            'off':   i,
            'L':     L,
            'seq':   seq,
            'sev':   sev,
            'ts':    ts,
            'flags': flags,
            'msgid': msgid,
            'text':  text,
        }
        i += 7 + L

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('infile')
    ap.add_argument('--tag-include', action='append', default=[], help='only frames whose text contains this substring (repeatable)')
    ap.add_argument('--tag-exclude', action='append', default=[], help='drop frames whose text contains this substring (repeatable)')
    ap.add_argument('--raw', action='store_true', help='include framing metadata in output')
    ap.add_argument('--from-ts', type=int, default=0, help='skip frames before this timestamp')
    ap.add_argument('--to-ts',   type=int, default=0, help='stop after this timestamp (0 = no limit)')
    args = ap.parse_args()

    data = open(args.infile, 'rb').read()
    n_in = n_out = 0
    for fr in parse_frames(data):
        n_in += 1
        if args.from_ts and fr['ts'] < args.from_ts: continue
        if args.to_ts and fr['ts'] > args.to_ts: continue   # not break — multi-clock streams aren't monotonic
        text = fr['text']
        if args.tag_include and not any(t in text for t in args.tag_include): continue
        if args.tag_exclude and any(t in text for t in args.tag_exclude): continue
        n_out += 1
        if args.raw:
            print(f"{fr['ts']:10d} seq={fr['seq'].decode('latin-1','replace')} sev=0x{fr['sev']:02x} mid={fr['msgid']:5d} | {text}")
        else:
            print(f"{fr['ts']:10d}  {text}")
    print(f"\n# parsed {n_in} frames, emitted {n_out}", file=sys.stderr)

if __name__ == '__main__':
    main()
