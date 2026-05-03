#!/usr/bin/env python3
"""
patch_adbd.py — Patch stock /sbin/adbd → adbd.patched so adbd does not drop
privileges to AID_SHELL on startup. After flashing the patched ramdisk, the
*intent* is that `adb shell` returns uid 0 directly.

╔══════════════════════════════════════════════════════════════════════════╗
║  ⚠  THIS PATCH HAS BEEN UNWIRED FROM innioasis-y1-fixes.bash (v1.7.0)     ║
║                                                                          ║
║  Both attempted approaches (NOP-the-blx and arg-zero) caused "device     ║
║  offline" on hardware: adbd starts and the USB endpoint enumerates,      ║
║  but the ADB protocol handshake never completes. Without on-device       ║
║  visibility (logcat / dmesg / strace, all of which require working      ║
║  ADB), we couldn't diagnose what about adbd-at-uid-0 breaks the          ║
║  protocol on this OEM build. The script and its analysis are kept here   ║
║  as historical record — do not ship the output of this patcher into a    ║
║  flashed boot.img unless you have first identified and addressed the     ║
║  root cause of the protocol-handshake failure (likely something in       ║
║  adbd's USB FFS init or a vendor-added uid check we missed statically).  ║
║                                                                          ║
║  Recovery if you accidentally flashed an adbd patched by this script:    ║
║  re-flash boot.img with the stock /sbin/adbd via mtkclient (BROM is      ║
║  independent of adbd, so the device is still flashable).                 ║
╚══════════════════════════════════════════════════════════════════════════╝

Stock binary md5:  9e7091f1699f89dc905dee3d9d5b23d8  (size: 223,132 bytes)
Output md5:        9eeb6b3bef1bef19b132936cc3b0b230  (arg-zero, current — broken)
Earlier output md5: ccebb66b25200f7e154ec23eb79ea9b4 (NOP-the-blx, superseded — also broken)

Binary: ARM32 ELF EXEC, statically linked, stripped.
        RX segment: file_off 0x0, vaddr 0x8000, size 0x34594.
        File offset = vaddr - 0x8000 inside the RX segment.

--- Why patch adbd at all ---

This OEM adbd has stripped the standard `should_drop_privileges()` gating from
its drop-privileges path. Stock AOSP adbd checks `ro.secure`, `ro.debuggable`,
and `service.adb.root` to decide whether to keep root, but on this binary the
drop happens unconditionally — the privilege-drop sequence at vaddr 0x94b8 is
called every time, regardless of any property. Setting ro.secure=0 etc. in
default.prop is therefore inert for the adbd-as-root question.

Confirmed by:
  - `strings adbd` returns ZERO references to "ro.secure" (would be present in
    a stock AOSP adbd that gates on it).
  - The drop_privileges block at 0x94b8 has no preceding conditional jump that
    skips it.
  - `adb shell id` on a device with ro.secure=0/ro.debuggable=1/ro.adb.secure=0
    in default.prop still returns uid=2000(shell). Confirmed 2026-05-03.

Running `adb root` is also actively harmful on the un-patched firmware: adbd
accepts the request (ro.debuggable=1 passes the permission check), sets
service.adb.root=1 and exits to be respawned by init. The respawned adbd hits
the same unconditional drop_privileges path and ends up at uid 2000 again —
but the self-restart cycle requires a USB rebind that the stock MTK adbd
handles poorly, and the host loses the device until reboot.

The only reliable fix is to patch the drop_privileges sequence in adbd itself.

--- The patches: arg-zero approach (revised 2026-05-03) ---

The drop_privileges block at vaddr 0x94b8 (file_off 0x14b8) in this OEM adbd:

    0x94b8:  movs    r0, #0xb            ; arg0 = count = 11
    0x94ba:  add     r1, sp, #0x24       ; arg1 = gid_array on stack
    0x94bc:  blx     #0x17038            ; setgroups(11, gids)
    0x94c0:  cmp     r0, #0
    0x94c2:  bne.w   #0x97ea             ; on failure → exit(1)
    0x94c6:  mov.w   r0, #0x7d0          ; arg0 = AID_SHELL = 2000
    0x94ca:  blx     #0x1701c            ; setgid(2000)
    0x94ce:  cmp     r0, #0
    0x94d0:  bne.w   #0x97ea             ; on failure → exit(1)
    0x94d4:  mov.w   r0, #0x7d0          ; arg0 = AID_SHELL = 2000
    0x94d8:  blx     #0x19418            ; setuid(2000) wrapper
    0x94dc:  mov     r3, r0              ; r3 = setuid return value (= 0 on success)
    0x94de:  cmp     r0, #0
    0x94e0:  bne.w   #0x97ea             ; on failure → exit(1)
    0x94e4:  ...                         ; continues with normal init

The current patches change the *argument loads* from "2000" / "11" to "0",
leaving the syscall calls intact:

    H1: movs r0, #0xb       → movs r0, #0       ; setgroups(0, _) — clears supp groups
    H2: mov.w r0, #0x7d0    → mov.w r0, #0      ; setgid(0)       — succeeds at EUID=0
    H3: mov.w r0, #0x7d0    → mov.w r0, #0      ; setuid(0)       — no-op at EUID=0

Net effect: each syscall executes (so the kernel and bionic libc complete
whatever bookkeeping they do — capability bounding-set adjustments, thread
credential synchronization, etc.), but the process ends up as uid=0 / gid=0
with no supplementary groups instead of uid=2000 / gid=2000 / shell groups.

--- Why arg-zero, not NOP-the-blx (history) ---

An earlier revision of this patch NOPed the three `blx` calls outright (each
4-byte BLX replaced with `movs r0, #0; nop`). On hardware that left adbd in a
broken state where the host saw "device offline" — adbd starts and the USB
endpoint comes up, but the protocol handshake never completes. Most likely
the bionic setuid wrapper at 0x19418 (which `bl`s 0x27b30 *before* reaching
the actual `mov r7, #0xd5 ; svc 0` syscall stub at 0x31a70) is doing
capability bounding-set work or thread-credential bookkeeping that downstream
adbd code depends on. Skipping that wrapper entirely produces a process that
is technically uid 0 but has inconsistent capabilities/credentials, and the
USB ADB protocol layer never fully initializes.

The arg-zero approach keeps every syscall and every bionic wrapper intact;
the only thing that changes is the argument values. setuid(0) when EUID is
already 0 is a no-op that runs all the same bookkeeping. Same for setgid(0).
setgroups(0, _) clears supplementary groups, which is the desired end state
anyway.

Verified blx targets:
  - 0x17038 → ARM-mode `mov r7, #0xce ; svc 0`  (setgroups32 EABI #206)
  - 0x1701c → ARM-mode `mov r7, #0xd6 ; svc 0`  (setgid32 EABI #214)
  - 0x19418 → ARM wrapper that eventually reaches `mov r7, #0xd5 ; svc 0`
              at 0x31a70 (setuid32 EABI #213) via bl 0x27b30

Patch IDs (current set):
  H1 = setgroups count 11 → 0  (movs r0 immediate at 0x14b8)
  H2 = setgid arg 2000 → 0     (mov.w r0 immediate at 0x14c6)
  H3 = setuid arg 2000 → 0     (mov.w r0 immediate at 0x14d4)

Usage:
    python3 patch_adbd.py adbd
    python3 patch_adbd.py adbd --output /tmp/adbd.patched
    python3 patch_adbd.py adbd --verify-only

Deploy:
    Repack into boot.img ramdisk (see patch_bootimg.py — it embeds these same
    patches and applies them in-place to /sbin/adbd inside the cpio stream).
"""

import argparse
import hashlib
import sys
from pathlib import Path

STOCK_MD5  = "9e7091f1699f89dc905dee3d9d5b23d8"
OUTPUT_MD5 = "9eeb6b3bef1bef19b132936cc3b0b230"

PATCHES = [
    {
        "name":   "[H1] setgroups count 11 -> 0  (movs r0,#0xb -> movs r0,#0)",
        "offset": 0x014b8,
        "before": bytes([0x0b, 0x20]),
        "after":  bytes([0x00, 0x20]),
    },
    {
        "name":   "[H2] setgid arg 2000 -> 0    (mov.w r0,#0x7d0 -> mov.w r0,#0)",
        "offset": 0x014c6,
        "before": bytes([0x4f, 0xf4, 0xfa, 0x60]),
        "after":  bytes([0x4f, 0xf0, 0x00, 0x00]),
    },
    {
        "name":   "[H3] setuid arg 2000 -> 0    (mov.w r0,#0x7d0 -> mov.w r0,#0)",
        "offset": 0x014d4,
        "before": bytes([0x4f, 0xf4, 0xfa, 0x60]),
        "after":  bytes([0x4f, 0xf0, 0x00, 0x00]),
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


def patch_bytes(data: bytes) -> bytes:
    """Apply all patches to a copy of `data` and return the patched bytes.

    Verifies each site matches the stock 'before' value first; raises ValueError
    if any site has unexpected bytes. Used by patch_bootimg.py to patch adbd
    in-place inside the boot.img ramdisk cpio.
    """
    buf = bytearray(data)
    for p in PATCHES:
        actual = bytes(buf[p["offset"]: p["offset"] + len(p["before"])])
        if actual != p["before"]:
            raise ValueError(
                f"{p['name']}: expected {p['before'].hex()} at 0x{p['offset']:x}, "
                f"got {actual.hex()}"
            )
        buf[p["offset"]: p["offset"] + len(p["after"])] = p["after"]
    return bytes(buf)


def main():
    parser = argparse.ArgumentParser(
        description="Patch stock /sbin/adbd to skip privilege-drop on startup"
    )
    parser.add_argument("input", help="Path to stock adbd")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path (default: output/adbd.patched)")
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
        print("\nERROR: patch sites match neither stock nor patched.")
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
        output_path = output_dir / "adbd.patched"
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
    print(f"\nDeploy: repack into boot.img ramdisk (see patch_bootimg.py).")


if __name__ == "__main__":
    main()
