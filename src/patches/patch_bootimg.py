#!/usr/bin/env python3
"""
patch_bootimg.py — Patch stock boot.img → boot.img.patched for ADB root access.

╔══════════════════════════════════════════════════════════════════════════╗
║  ⚠  THIS SCRIPT IS NO LONGER WIRED INTO apply.bash (v1.7.0). ║
║                                                                          ║
║  The /sbin/adbd binary patches it embeds (via patch_adbd.patch_bytes)    ║
║  caused "device offline" on hardware in every revision tried.            ║
║  See the warning header in patch_adbd.py for details. The boot.img       ║
║  format-aware cpio patcher itself works correctly (default.prop edits    ║
║  + in-place adbd replacement, cpio round-trip verified end-to-end);      ║
║  the issue is purely with the adbd byte patches it applies.              ║
║                                                                          ║
║  Kept as historical record. To re-wire: re-add the boot.img extraction   ║
║  block + patch_bootimg invocation + boot.img mtkclient flash to          ║
║  apply.bash, AND fix the underlying adbd-at-uid-0           ║
║  protocol failure first (don't ship this patcher's output otherwise).    ║
╚══════════════════════════════════════════════════════════════════════════╝

Two changes are applied to the ramdisk:

1. Edit `default.prop` so the standard rooted-ROM properties are set:
       ro.secure        = 0
       ro.debuggable    = 1
       ro.adb.secure    = 0

2. Patch `/sbin/adbd` to neutralize its unconditional privilege-drop
   sequence. See `patch_adbd.py` for the detailed analysis — short version:
   this OEM adbd has stripped the `should_drop_privileges()` gating, so the
   default.prop edits are inert for the adbd-as-root question. The current
   approach changes the *argument values* of the three calls in adbd's
   drop_privileges block (vaddr 0x94b8, file_off 0x14b8) from 2000/11 to 0:
   `setgroups(0, _)`, `setgid(0)`, `setuid(0)`. The syscalls still execute
   so all bionic bookkeeping (capability bounding-set, thread-credential
   sync) runs normally; the process just ends up at uid=0/gid=0 with no
   supplementary groups. After this patch + flash, `adb shell` returns uid 0
   directly and `adb root` is neither needed nor harmful (adbd reports
   "already running as root"). An earlier attempt that NOPed the blx calls
   outright caused "device offline" — the bionic setuid wrapper at 0x19418
   does work that downstream adbd code depends on.

Format-aware: handles the Android boot.img wrapper *and* the MTK 512-byte
"ROOTFS" header that wraps the gzipped cpio ramdisk on MT65xx devices.
Patches `default.prop` in-place inside the cpio stream — no extract/repack of
device nodes, which is where the previous bash-based attempt drifted.

Stock initrd MTK header (verified on Innioasis Y1 firmware 3.0.2):
    0x000  88 16 88 58       magic
    0x004  <u32 LE>          payload size (gzipped cpio bytes after the header)
    0x008  "ROOTFS\0..."     32-byte name field
    0x028  ff ff ff ...      0xff padding to 512

Android boot.img v0 header (page_size 0x800 on Y1):
    8s   ANDROID! magic
    u32  kernel_size, kernel_addr
    u32  ramdisk_size, ramdisk_addr
    u32  second_size, second_addr
    u32  tags_addr, page_size
    u32  unused[2]
    16s  name
    512s cmdline
    32s  id (sha1)
    1024s extra_cmdline
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import re
import struct
import sys
from pathlib import Path

import patch_adbd


ANDROID_BOOT_MAGIC = b"ANDROID!"
MTK_RAMDISK_MAGIC = b"\x88\x16\x88\x58"
MTK_RAMDISK_NAME = b"ROOTFS"
MTK_RAMDISK_HDR_LEN = 512
CPIO_MAGIC_NEWC = b"070701"
CPIO_MAGIC_NEWC_CRC = b"070702"
CPIO_TRAILER = "TRAILER!!!"


def _pad_to(blob: bytes, page_size: int) -> bytes:
    rem = len(blob) % page_size
    if rem == 0:
        return blob
    return blob + b"\x00" * (page_size - rem)


def _align4(n: int) -> int:
    return (n + 3) & ~3


# -- Android boot.img --------------------------------------------------------


class BootImg:
    def __init__(self, blob: bytes):
        if blob[:8] != ANDROID_BOOT_MAGIC:
            raise ValueError("input is not an Android boot.img (missing ANDROID! magic)")
        (
            self.kernel_size,
            self.kernel_addr,
            self.ramdisk_size,
            self.ramdisk_addr,
            self.second_size,
            self.second_addr,
            self.tags_addr,
            self.page_size,
            _u0,
            _u1,
        ) = struct.unpack_from("<10I", blob, 8)
        self.name = blob[48:64]
        self.cmdline = blob[64 : 64 + 512]
        self.id = blob[64 + 512 : 64 + 512 + 32]
        self.extra_cmdline = blob[64 + 512 + 32 : 64 + 512 + 32 + 1024]

        ps = self.page_size
        n_kernel = ((self.kernel_size + ps - 1) // ps) * ps
        n_ramdisk = ((self.ramdisk_size + ps - 1) // ps) * ps
        n_second = ((self.second_size + ps - 1) // ps) * ps
        off = ps
        self.kernel = blob[off : off + self.kernel_size]
        off += n_kernel
        self.ramdisk = blob[off : off + self.ramdisk_size]
        off += n_ramdisk
        self.second = blob[off : off + self.second_size]

    def repack(self, kernel: bytes, ramdisk: bytes) -> bytes:
        ps = self.page_size
        sha = hashlib.sha1()
        sha.update(kernel)
        sha.update(struct.pack("<I", len(kernel)))
        sha.update(ramdisk)
        sha.update(struct.pack("<I", len(ramdisk)))
        sha.update(self.second)
        sha.update(struct.pack("<I", len(self.second)))
        new_id = sha.digest() + b"\x00" * (32 - sha.digest_size)

        hdr = bytearray()
        hdr += ANDROID_BOOT_MAGIC
        hdr += struct.pack(
            "<10I",
            len(kernel), self.kernel_addr,
            len(ramdisk), self.ramdisk_addr,
            len(self.second), self.second_addr,
            self.tags_addr, ps,
            0, 0,
        )
        hdr += self.name
        hdr += self.cmdline
        hdr += new_id
        hdr += self.extra_cmdline
        assert len(hdr) <= ps, f"header {len(hdr)} > page_size {ps}"
        out = bytes(hdr) + b"\x00" * (ps - len(hdr))
        out += _pad_to(kernel, ps)
        out += _pad_to(ramdisk, ps)
        if self.second:
            out += _pad_to(self.second, ps)
        return out


# -- MTK ramdisk wrapper -----------------------------------------------------


def mtk_unwrap(rd: bytes) -> bytes:
    if rd[:4] != MTK_RAMDISK_MAGIC:
        return rd
    declared = int.from_bytes(rd[4:8], "little")
    payload = rd[MTK_RAMDISK_HDR_LEN : MTK_RAMDISK_HDR_LEN + declared]
    if len(payload) != declared:
        raise ValueError(
            f"MTK ramdisk header declares {declared} bytes but only {len(payload)} present"
        )
    return payload


def mtk_wrap(payload: bytes) -> bytes:
    hdr = bytearray(MTK_RAMDISK_HDR_LEN)
    hdr[0:4] = MTK_RAMDISK_MAGIC
    hdr[4:8] = len(payload).to_bytes(4, "little")
    hdr[8 : 8 + len(MTK_RAMDISK_NAME)] = MTK_RAMDISK_NAME
    for i in range(40, MTK_RAMDISK_HDR_LEN):
        hdr[i] = 0xFF
    return bytes(hdr) + payload


# -- cpio newc in-place patch ------------------------------------------------


def cpio_replace_file(cpio: bytes, target_name: str, new_content: bytes) -> bytes:
    """Replace the content of `target_name` inside an SVR4 newc cpio stream.

    Walks records sequentially, rewrites the matching record's filesize header
    field + payload, and copies all other records verbatim. Padding is recomputed
    per record. cpio has no offset table — record-by-record splicing is safe.
    """
    out = bytearray()
    i = 0
    found = False
    while i < len(cpio):
        magic = cpio[i : i + 6]
        if magic not in (CPIO_MAGIC_NEWC, CPIO_MAGIC_NEWC_CRC):
            # Trailing zero padding after TRAILER!!! is normal — copy it through.
            out += cpio[i:]
            break

        # 13 ASCII-hex 8-char fields after the 6-char magic.
        def field(idx: int) -> int:
            off = i + 6 + idx * 8
            return int(cpio[off : off + 8], 16)

        filesize = field(6)
        namesize = field(11)
        name_end = i + 110 + namesize  # name is null-terminated, namesize includes \0
        name = cpio[i + 110 : name_end - 1].decode("ascii", "replace")
        hdr_padded_end = i + _align4(110 + namesize)
        content_end = hdr_padded_end + filesize
        rec_padded_end = i + _align4(content_end - i)

        if name == target_name:
            new_filesize = len(new_content)
            new_hdr = bytearray(cpio[i : i + 110])
            new_hdr[6 + 6 * 8 : 6 + 7 * 8] = f"{new_filesize:08X}".encode("ascii")
            out += bytes(new_hdr)
            out += cpio[i + 110 : hdr_padded_end]  # name + name-padding (unchanged)
            out += new_content
            pad = _align4(new_filesize) - new_filesize
            out += b"\x00" * pad
            found = True
        else:
            out += cpio[i:rec_padded_end]

        if name == CPIO_TRAILER:
            # Copy trailing zero padding (some cpio writers pad to 512).
            out += cpio[rec_padded_end:]
            break
        i = rec_padded_end

    if not found:
        raise RuntimeError(f"cpio: {target_name!r} not found")
    return bytes(out)


# -- default.prop edits ------------------------------------------------------


_DEFAULT_PROP_EDITS = [
    ("ro.secure", "0"),
    ("ro.debuggable", "1"),
    ("ro.adb.secure", "0"),
]


def patch_default_prop(blob: bytes) -> bytes:
    text = blob.decode("utf-8")
    for key, val in _DEFAULT_PROP_EDITS:
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
        if pattern.search(text):
            text = pattern.sub(f"{key}={val}", text)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += f"{key}={val}\n"
    return text.encode("utf-8")


# -- gzip helpers (deterministic mtime=0) ------------------------------------


def gunzip(blob: bytes) -> bytes:
    return gzip.decompress(blob)


def gzip_bytes(blob: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as g:
        g.write(blob)
    return buf.getvalue()


# -- top-level ---------------------------------------------------------------


def patch_bootimg(src: bytes) -> bytes:
    boot = BootImg(src)
    print(f"  page_size:    0x{boot.page_size:x}")
    print(f"  kernel:       {boot.kernel_size} bytes @ 0x{boot.kernel_addr:08x}")
    print(f"  ramdisk:      {boot.ramdisk_size} bytes @ 0x{boot.ramdisk_addr:08x}")
    if boot.second_size:
        print(f"  second:       {boot.second_size} bytes @ 0x{boot.second_addr:08x}")

    gz_cpio = mtk_unwrap(boot.ramdisk)
    cpio = gunzip(gz_cpio)

    new_default_prop = patch_default_prop(cpio_extract_file(cpio, "default.prop"))
    print(f"  default.prop: {len(new_default_prop)} bytes after patch")
    cpio2 = cpio_replace_file(cpio, "default.prop", new_default_prop)

    stock_adbd = cpio_extract_file(cpio2, "sbin/adbd")
    new_adbd = patch_adbd.patch_bytes(stock_adbd)
    print(f"  sbin/adbd:    {len(new_adbd)} bytes after patch (md5 {hashlib.md5(new_adbd).hexdigest()})")
    cpio3 = cpio_replace_file(cpio2, "sbin/adbd", new_adbd)

    new_gz = gzip_bytes(cpio3)
    new_ramdisk = mtk_wrap(new_gz) if boot.ramdisk[:4] == MTK_RAMDISK_MAGIC else new_gz

    out = boot.repack(boot.kernel, new_ramdisk)
    return out


def cpio_extract_file(cpio: bytes, target_name: str) -> bytes:
    i = 0
    while i < len(cpio):
        if cpio[i : i + 6] not in (CPIO_MAGIC_NEWC, CPIO_MAGIC_NEWC_CRC):
            break

        def field(idx: int) -> int:
            off = i + 6 + idx * 8
            return int(cpio[off : off + 8], 16)

        filesize = field(6)
        namesize = field(11)
        name = cpio[i + 110 : i + 110 + namesize - 1].decode("ascii", "replace")
        hdr_padded_end = i + _align4(110 + namesize)
        content_end = hdr_padded_end + filesize
        if name == target_name:
            return cpio[hdr_padded_end:content_end]
        if name == CPIO_TRAILER:
            break
        i = _align4(content_end - i) + i
    raise RuntimeError(f"cpio: {target_name!r} not found")


def main() -> int:
    ap = argparse.ArgumentParser(description="Patch boot.img ramdisk for ADB root access.")
    ap.add_argument("--in", dest="src", required=True, type=Path, help="source boot.img")
    ap.add_argument("--out", dest="dst", required=True, type=Path, help="output boot.img.patched")
    args = ap.parse_args()

    print(f"Reading {args.src}..")
    src = args.src.read_bytes()
    print(f"Patching ({len(src)} bytes)..")
    out = patch_bootimg(src)
    args.dst.write_bytes(out)
    print(f"Wrote {args.dst} ({len(out)} bytes)")
    print(f"  md5: {hashlib.md5(out).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
