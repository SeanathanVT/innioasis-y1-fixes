#!/usr/bin/env python3
"""Reconstruct AVRCP frames from mtkbt's btlog.bin PutByte/GetByte text records.

btlog truncates the byte-trace per record (~20 B regardless of claimed
`len`), so long PDU payloads (e.g. GetElementAttributes strings) are
partial. Still enough to identify PDU id, event id, reason code, and
the first few bytes of the param payload.

Usage:
  btlog-hci-extract.py <btlog.bin>                # all frames
  btlog-hci-extract.py <btlog.bin> --avrcp        # only AVRCP-recognized
  btlog-hci-extract.py <btlog.bin> --pdu 0x31     # only RegisterNotification
"""

import argparse
import re
import struct
import sys

PRELUDE = b"connected to @btlog, dumping..."
SYNC = b"\x55\x00"

# Match a btlog record header: sync (0x55 0x00) + 4-byte ID + 2-byte
# upper-ID/seq + 4-byte LE timestamp + 4-byte "0a 00 00 00" pad.
# The `0a 00 00 00` pad is the stable structural anchor — early
# parser builds keyed on bytes 6..7 == "0x03 0x00", which only held
# for a narrow ID range in one capture.
RECORD_HEADER_RE = re.compile(
    rb"\x55\x00(.{4})(.{2})(.{4})\x0a\x00\x00\x00", re.DOTALL
)

# Within a record: text payload starts after a 16-bit length prefix.
DIRECTION_RE = re.compile(rb"\[BT\](PutByte|GetByte): len=(\d+)")
HEX_BYTES_RE = re.compile(rb"\[BT\] (?:, [0-9a-fA-F]+)+")

# AVRCP PDU names (1.3 + 1.4 most common).
PDU_NAMES = {
    0x10: "GetCapabilities",
    0x11: "ListPlayerApplicationSettingAttributes",
    0x12: "ListPlayerApplicationSettingValues",
    0x13: "GetCurrentPlayerApplicationSettingValue",
    0x14: "SetPlayerApplicationSettingValue",
    0x15: "GetPlayerApplicationSettingAttributeText",
    0x16: "GetPlayerApplicationSettingValueText",
    0x17: "InformDisplayableCharacterSet",
    0x18: "InformBatteryStatusOfCT",
    0x20: "GetElementAttributes",
    0x30: "GetPlayStatus",
    0x31: "RegisterNotification",
    0x40: "RequestContinuingResponse",
    0x41: "AbortContinuingResponse",
    0x50: "SetAbsoluteVolume",
    0x60: "SetAddressedPlayer",
    0x70: "GetFolderItems",
    0x71: "ChangePath",
    0x72: "GetItemAttributes",
    0x73: "PlayItem",
    0x74: "GetTotalNumberOfItems",
    0x80: "Search",
    0x90: "AddToNowPlaying",
    0xa0: "GeneralReject",
}

EVENT_NAMES = {
    0x01: "PLAYBACK_STATUS_CHANGED",
    0x02: "TRACK_CHANGED",
    0x03: "TRACK_REACHED_END",
    0x04: "TRACK_REACHED_START",
    0x05: "PLAYBACK_POS_CHANGED",
    0x06: "BATT_STATUS_CHANGED",
    0x07: "SYSTEM_STATUS_CHANGED",
    0x08: "PLAYER_APPLICATION_SETTING_CHANGED",
    0x09: "NOW_PLAYING_CONTENT_CHANGED",
    0x0a: "AVAILABLE_PLAYERS_CHANGED",
    0x0b: "ADDRESSED_PLAYER_CHANGED",
    0x0c: "UIDS_CHANGED",
    0x0d: "VOLUME_CHANGED",
}

CTYPE_NAMES = {
    0x00: "CONTROL",
    0x01: "STATUS",
    0x02: "SPECIFIC_INQUIRY",
    0x03: "NOTIFY",
    0x04: "GENERAL_INQUIRY",
    0x08: "NOT_IMPLEMENTED",
    0x09: "ACCEPTED",
    0x0a: "REJECTED",
    0x0b: "IN_TRANSITION",
    0x0c: "STABLE",
    0x0d: "CHANGED",
    0x0f: "INTERIM",
}


def parse_records(data):
    """Yield (timestamp_ms, payload_text) for each btlog record.

    Layout: sync(0x55 0x00) + 4-B id + 2-B seq + ts(u32 LE) + 4-B header-rest +
    payload_len(u16 LE) + 2-B tag + payload_text + 1-B trailer.

    The 4 bytes at offset 12..15 vary across firmware (early captures saw
    "0a 00 00 00", later ones see "00 00 00 00", and individual records
    drift further), so we can't anchor on them. Instead, accept a record
    if its declared payload_len is plausible AND the next sync appears
    within a small trailer window after the consumed record. Wrong
    alignments fail one or both checks.
    """
    i = data.find(PRELUDE)
    if i >= 0:
        i = data.find(b"\n", i) + 1
    else:
        i = 0

    while True:
        sync = data.find(SYNC, i)
        if sync < 0 or sync + 20 > len(data):
            return
        payload_len = struct.unpack_from("<H", data, sync + 16)[0]
        if payload_len > 8192 or sync + 20 + payload_len > len(data):
            i = sync + 2
            continue
        # Validate alignment: another sync must appear within 16 bytes
        # after the declared record end (trailer is normally 1 byte).
        rec_end = sync + 20 + payload_len
        if rec_end < len(data) - 32:
            next_sync = data.find(SYNC, rec_end, rec_end + 16)
            if next_sync < 0:
                i = sync + 2
                continue
        timestamp = struct.unpack_from("<I", data, sync + 8)[0]
        text = data[sync + 20 : sync + 20 + payload_len]
        yield (timestamp, text)
        i = sync + 20 + payload_len + 1  # skip past trailer


def parse_byte_records(data):
    """Yield (timestamp, direction, claimed_len, bytes_list) for each
    PutByte/GetByte transaction. Pairs the direction-header record with
    the immediately-following "[BT] , h, h, h, ..." record's hex bytes."""
    records = list(parse_records(data))
    for idx, (ts, text) in enumerate(records):
        m_dir = DIRECTION_RE.search(text)
        if not m_dir:
            continue
        direction = "TX" if m_dir.group(1) == b"PutByte" else "RX"
        claim = int(m_dir.group(2))
        # Look at the next 1-3 records for the hex payload.
        bytes_list = []
        for k in range(1, 4):
            if idx + k >= len(records):
                break
            m_hex = HEX_BYTES_RE.search(records[idx + k][1])
            if m_hex:
                pieces = m_hex.group(0).decode("latin1").split(",")[1:]
                bytes_list = [int(p.strip(), 16) for p in pieces if p.strip()]
                break
        yield (ts, direction, claim, bytes_list)


def decode_avrcp(payload):
    """Given an HCI ACL frame's raw bytes, decode any AVRCP info.

    Returns a human-readable suffix string, or None if not AVRCP."""
    if len(payload) < 5 or payload[0] != 0x02:
        return None
    # HCI ACL: type(1) handle(2) data_len(2)
    # L2CAP: length(2) cid(2)
    if len(payload) < 9:
        return None
    cid = payload[7] | (payload[8] << 8)
    # AVRCP control channel is dynamic but usually 0x004d / 0x0043.
    # We just check for AVCTP PID 0x110e at L2CAP payload offset 1.
    if len(payload) < 12:
        return None
    pid = (payload[10] << 8) | payload[11]
    if pid != 0x110E:
        return f"cid=0x{cid:04x} (non-AVRCP L2CAP)"
    # AV/C header
    if len(payload) < 15:
        return f"AVCTP cid=0x{cid:04x}"
    ctype = payload[12] & 0x0F
    ctype_name = CTYPE_NAMES.get(ctype, f"ctype=0x{ctype:x}")
    if len(payload) < 19:
        return f"AVCTP cid=0x{cid:04x} {ctype_name}"
    # AV/C VENDOR_DEPENDENT layout from payload index 9 onward:
    #   9    AVCTP byte (TL/PT/CR/IPID)
    #   10-11 PID (BE)
    #   12   AV/C ctype (low nibble)
    #   13   subunit_type|subunit_id
    #   14   opcode (0x00 = VENDOR_DEPENDENT)
    #   15-17 vendor BT-SIG (0x00 0x19 0x58)
    #   18   PDU ID
    #   19   reserved (0x00)
    #   20-21 param length (BE)
    #   22+  PDU payload (event_id for RegisterNotification responses, etc.)
    pdu = payload[18]
    pdu_name = PDU_NAMES.get(pdu, f"PDU=0x{pdu:02x}")
    extra = ""
    if pdu == 0x31 and len(payload) > 22:
        event_id = payload[22]
        evt_name = EVENT_NAMES.get(event_id, f"event=0x{event_id:02x}")
        extra = f" {evt_name}"
    elif pdu == 0x20 and len(payload) > 30:
        # GetElementAttributes: id8 (22..29) + num_attrs (30) + attrs...
        identifier = " ".join(f"{b:02x}" for b in payload[22:30])
        extra = f" id={identifier}"
    return f"cid=0x{cid:04x} {ctype_name} {pdu_name}{extra}"


def main():
    ap = argparse.ArgumentParser(
        description="Extract HCI byte streams from mtkbt btlog.bin",
    )
    ap.add_argument("infile")
    ap.add_argument(
        "--avrcp", action="store_true", help="only emit AVRCP-recognized frames"
    )
    ap.add_argument(
        "--pdu", type=lambda s: int(s, 0), help="filter to specific PDU id"
    )
    args = ap.parse_args()

    with open(args.infile, "rb") as f:
        data = f.read()

    count = 0
    for ts, direction, claim, bytes_list in parse_byte_records(data):
        avrcp = decode_avrcp(bytes_list)
        if args.avrcp and (avrcp is None or "non-AVRCP" in avrcp):
            continue
        if args.pdu is not None:
            if not bytes_list or len(bytes_list) <= 18 or bytes_list[18] != args.pdu:
                continue
        hex_str = " ".join(f"{b:02x}" for b in bytes_list[:32])
        suffix = "" if len(bytes_list) <= 32 else " ..."
        trunc = "" if len(bytes_list) >= claim else f" [+{claim-len(bytes_list)} not logged]"
        avrcp_str = f"  ← {avrcp}" if avrcp else ""
        print(
            f"{ts:10d} {direction} len={claim:3d} got={len(bytes_list):3d}{trunc}: {hex_str}{suffix}{avrcp_str}"
        )
        count += 1

    print(f"\n# {count} record(s) emitted", file=sys.stderr)


if __name__ == "__main__":
    main()
