"""Minimal binary AXML editor for Android manifest patching.

The Android binary XML (AXML) format is a chunked file:
  - XMLFileHeader (8 B): magic 0x00080003 + total file size
  - StringPool chunk: header + per-string offsets + UTF-16 (or UTF-8) string data
  - ResourceMap chunk: array of Android resource IDs, one per leading string
  - StartNamespace chunk (xmlns:android="…")
  - Stream of Start/End element + (text) chunks
  - EndNamespace chunk

This module supports the narrow set of operations the music-APK patcher needs:
  1. Read the file → parse strings, chunk offsets.
  2. Append new strings to the pool (existing indices stay valid).
  3. Build StartElement / EndElement chunks (with attributes that reference
     either strings or Android resource IDs).
  4. Insert the new chunks just before a chosen existing chunk
     (typically the </application> EndElement).
  5. Re-emit the file with updated string pool, chunk sizes, file size.

Limitations (acceptable for this project):
  - UTF-16 string pool only (the music-APK manifest is UTF-16; we don't add
    UTF-8 support until something needs it).
  - No ResourceMap mutation (we only append strings beyond the resource-mapped
    prefix, so the existing ResourceMap stays correct).
  - No CDATA handling.
  - No mid-file string deletion / reordering.

AXML reference: AOSP `frameworks/base/include/androidfw/ResourceTypes.h`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Tuple

# --- Chunk type constants -----------------------------------------------------
RES_XML_TYPE = 0x0003
RES_STRING_POOL_TYPE = 0x0001
RES_XML_RESOURCE_MAP_TYPE = 0x0180
RES_XML_START_NAMESPACE_TYPE = 0x0100
RES_XML_END_NAMESPACE_TYPE = 0x0101
RES_XML_START_ELEMENT_TYPE = 0x0102
RES_XML_END_ELEMENT_TYPE = 0x0103
RES_XML_CDATA_TYPE = 0x0104

# Res_value dataTypes (we use a small subset)
TYPE_REFERENCE = 0x01
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_BOOLEAN = 0x12

# StringPool flag bits
STRING_POOL_UTF8_FLAG = 0x100

NULL_REF = 0xFFFFFFFF


@dataclass
class Chunk:
    """One AXML chunk (raw bytes, retained verbatim except for size patches)."""
    type: int
    raw: bytes  # full chunk bytes including header


@dataclass
class AxmlFile:
    is_utf8: bool
    strings: List[str]                # one entry per string-pool slot
    string_pool_flags: int            # raw flags word (UTF-8/sorted/etc)
    resource_map_raw: bytes           # entire ResourceMap chunk including header
    chunks: List[Chunk]               # everything after the ResourceMap


# ---------- string pool --------------------------------------------------------

def _read_string_pool(buf: bytes, off: int) -> Tuple[List[str], int, int]:
    """Return (strings, flags, chunk_size)."""
    chunk_type, _, chunk_size = struct.unpack_from('<HHI', buf, off)
    assert chunk_type == RES_STRING_POOL_TYPE, f'expected StringPool, got 0x{chunk_type:x}'
    string_count, _style_count, flags, strings_start, _styles_start = \
        struct.unpack_from('<IIIII', buf, off + 8)
    if flags & STRING_POOL_UTF8_FLAG:
        raise NotImplementedError('UTF-8 string pools not supported')
    string_offsets = struct.unpack_from(f'<{string_count}I', buf, off + 28)
    data_start = off + strings_start
    strings: List[str] = []
    for so in string_offsets:
        p = data_start + so
        n = struct.unpack_from('<H', buf, p)[0]
        if n & 0x8000:
            # Two-halfword length (huge strings); not expected in practice.
            n = ((n & 0x7FFF) << 16) | struct.unpack_from('<H', buf, p + 2)[0]
            p += 4
        else:
            p += 2
        strings.append(buf[p:p + n * 2].decode('utf-16-le'))
    return strings, flags, chunk_size


def _serialize_string_pool_utf16(strings: List[str], flags: int) -> bytes:
    """Serialize a UTF-16 string pool chunk. Returns bytes including chunk header."""
    string_count = len(strings)
    header_size = 28                              # chunk header (8) + pool header (20)
    offsets_size = string_count * 4               # one uint32 per string

    # Each string: u16 length-in-chars + UTF-16-LE chars + u16 null terminator.
    # The data block must be 4-byte aligned at the end (pad with zeros).
    data_parts = []
    cursor = 0
    string_offsets = []
    for s in strings:
        encoded = s.encode('utf-16-le')
        n = len(encoded) // 2                     # length in u16 units
        if n >= 0x8000:
            raise NotImplementedError(f'string longer than 32767 units: {n}')
        string_offsets.append(cursor)
        entry = struct.pack('<H', n) + encoded + b'\x00\x00'
        data_parts.append(entry)
        cursor += len(entry)
    data = b''.join(data_parts)
    # 4-byte align the data section.
    pad = (-len(data)) & 3
    data += b'\x00' * pad

    strings_start = header_size + offsets_size
    chunk_size = strings_start + len(data)

    out = struct.pack('<HHI', RES_STRING_POOL_TYPE, header_size, chunk_size)
    out += struct.pack('<IIIII',
                       string_count,
                       0,                          # style_count
                       flags,
                       strings_start,
                       0)                          # styles_start
    out += struct.pack(f'<{string_count}I', *string_offsets)
    out += data
    return out


# ---------- top-level file -----------------------------------------------------

def read(path: str) -> AxmlFile:
    buf = open(path, 'rb').read()
    magic, _file_size = struct.unpack_from('<II', buf, 0)
    if magic != 0x00080003:
        raise ValueError(f'not an AXML file: magic=0x{magic:08x}')
    strings, flags, sp_size = _read_string_pool(buf, 8)

    # Parse the rest of the chunks. ResourceMap is kept verbatim; everything
    # after lives in self.chunks so callers can insert / scan.
    chunks: List[Chunk] = []
    off = 8 + sp_size
    resource_map_raw = b''
    while off < len(buf):
        chunk_type, _, chunk_size = struct.unpack_from('<HHI', buf, off)
        raw = buf[off:off + chunk_size]
        if chunk_type == RES_XML_RESOURCE_MAP_TYPE and not resource_map_raw:
            resource_map_raw = raw
        else:
            chunks.append(Chunk(type=chunk_type, raw=raw))
        off += chunk_size

    return AxmlFile(
        is_utf8=False,
        strings=strings,
        string_pool_flags=flags,
        resource_map_raw=resource_map_raw,
        chunks=chunks,
    )


def write(axml: AxmlFile, path: str) -> None:
    sp = _serialize_string_pool_utf16(axml.strings, axml.string_pool_flags)
    body = sp + axml.resource_map_raw + b''.join(c.raw for c in axml.chunks)
    file_size = 8 + len(body)
    header = struct.pack('<II', 0x00080003, file_size)
    open(path, 'wb').write(header + body)


# ---------- chunk builders -----------------------------------------------------

def _string_or_append(axml: AxmlFile, s: str) -> int:
    """Return the string-pool index of s, appending it past the resource-mapped
    prefix so existing indices stay valid."""
    try:
        return axml.strings.index(s)
    except ValueError:
        axml.strings.append(s)
        return len(axml.strings) - 1


def find_chunk_index(axml: AxmlFile, predicate) -> int:
    """First chunk index where predicate(chunk) is true; -1 if none."""
    for i, c in enumerate(axml.chunks):
        if predicate(c):
            return i
    return -1


def chunk_element_name(axml: AxmlFile, c: Chunk) -> str | None:
    """Tag name for a Start/End element chunk, else None."""
    if c.type not in (RES_XML_START_ELEMENT_TYPE, RES_XML_END_ELEMENT_TYPE):
        return None
    name_idx = struct.unpack_from('<I', c.raw, 16 + 4)[0]   # chunk hdr 8 + node 8, +4 for ns
    return axml.strings[name_idx] if 0 <= name_idx < len(axml.strings) else None


def _attr(ns_idx: int, name_idx: int, raw_value_idx: int,
          data_type: int, data: int) -> bytes:
    """One attribute record (20 B)."""
    return struct.pack('<IIIHBBI',
                       ns_idx,
                       name_idx,
                       raw_value_idx,        # 0xFFFFFFFF if not string
                       8,                    # typedValue.size
                       0,                    # typedValue.res0
                       data_type,
                       data)


def attr_string(axml: AxmlFile, ns_idx: int, name_idx: int, value: str) -> bytes:
    v_idx = _string_or_append(axml, value)
    return _attr(ns_idx, name_idx, v_idx, TYPE_STRING, v_idx)


def attr_bool(_axml: AxmlFile, ns_idx: int, name_idx: int, value: bool) -> bytes:
    return _attr(ns_idx, name_idx, NULL_REF, TYPE_INT_BOOLEAN,
                 0xFFFFFFFF if value else 0x00000000)


def attr_int(_axml: AxmlFile, ns_idx: int, name_idx: int, value: int) -> bytes:
    return _attr(ns_idx, name_idx, NULL_REF, TYPE_INT_DEC, value & 0xFFFFFFFF)


def start_element(axml: AxmlFile, name: str, line_no: int,
                  attrs: List[bytes],
                  ns_idx: int = NULL_REF,
                  id_index: int = 0,
                  class_index: int = 0,
                  style_index: int = 0) -> Chunk:
    name_idx = _string_or_append(axml, name)
    header_size = 16            # chunk header (8) + node (8) — attrExt starts after
    attr_struct_size = 20
    attr_data = b''.join(attrs)
    chunk_size = header_size + 20 + len(attr_data)
    raw = struct.pack('<HHI', RES_XML_START_ELEMENT_TYPE, header_size, chunk_size)
    raw += struct.pack('<II', line_no, NULL_REF)        # node: lineNumber, comment
    raw += struct.pack('<II', ns_idx, name_idx)         # attrExt: ns, name
    raw += struct.pack('<HHHHHH',
                       20,                              # attrStart (from attrExt)
                       attr_struct_size,
                       len(attrs),
                       id_index, class_index, style_index)
    raw += attr_data
    return Chunk(type=RES_XML_START_ELEMENT_TYPE, raw=raw)


def end_element(axml: AxmlFile, name: str, line_no: int,
                ns_idx: int = NULL_REF) -> Chunk:
    name_idx = _string_or_append(axml, name)
    header_size = 16
    chunk_size = 24
    raw = struct.pack('<HHI', RES_XML_END_ELEMENT_TYPE, header_size, chunk_size)
    raw += struct.pack('<II', line_no, NULL_REF)
    raw += struct.pack('<II', ns_idx, name_idx)
    return Chunk(type=RES_XML_END_ELEMENT_TYPE, raw=raw)


def android_namespace_idx(axml: AxmlFile) -> int:
    """String-pool index for the android: namespace URI."""
    try:
        return axml.strings.index('http://schemas.android.com/apk/res/android')
    except ValueError as e:
        raise ValueError('manifest has no android xmlns binding') from e
