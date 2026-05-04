#!/usr/bin/env python3
"""
patch_mtkbt_pixel_shape.py — EXPERIMENT only. Wraps src/patches/patch_mtkbt.py
and overrides B1-B3 / C1-C3 / E3-E4 to match the SDP record shape that the
Pixel 4 (Android 10+) advertises for AVRCP TG.

Hypothesis (Trace #12, in-progress): the cardinality:0 / silent-after-AVCTP_EVENT:4
pattern reproduces against any 1.4-aware CT (Sonos, car) because Y1's served SDP
record is internally inconsistent vs. the modern AOSP/Pixel reference. Specifically:

   Attribute             Y1 (current)            Pixel 4 (works with Sonos)
   -----------------     -----------------       -------------------------
   ProtocolDescList      AVCTP 1.3 (0x0103)      AVCTP 1.4 (0x0104)
   ProfileDescList       AVRCP 1.4 (0x0104)      AVRCP 1.5 (0x0105)
   SupportedFeatures     0x0033                  0x00d1
                         Cat1+Cat2+PAS+GroupNav  Cat1+PAS+Browsing+MultiPlayer

The 0x0033 ↔ 0x00d1 difference is the load-bearing one — they overlap in only
0x11 (Cat1+PAS). The brief's E3/E4 baseline targets an older AOSP convention
that the Pixel-era stack moved away from.

This script is invocation-compatible with patch_mtkbt.py:
    python3 patch_mtkbt_pixel_shape.py <stock_mtkbt> --output <patched_mtkbt>

It imports the standard patch list, mutates only the targeted entries in memory
by offset, and applies them via the standard apply/verify pipeline.
patch_mtkbt.py is not modified on disk.

NB: This experiment does NOT serve AdditionalProtocolDescriptorList (0x000d) on
the wire — Group 2's last-wins still drops the browse PSM. So the 0x40 Browsing
bit we set is technically inconsistent with what we serve, exactly as the brief
warned about. We do this anyway because Pixel does NOT serve
AdditionalProtocolDescriptorList in its standard records either (verified from
the dump) — so matching its shape is the test, even if it claims more than it
serves.

Patched MD5 will differ from the standard `d47c904063e7d201f626cf2cc3ebd50b`.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if REPO_ROOT.name == "tools":
    REPO_ROOT = REPO_ROOT.parent
elif REPO_ROOT.name == "patches":
    REPO_ROOT = REPO_ROOT.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src" / "patches"))

try:
    import patch_mtkbt_orig as patch_mtkbt  # bash-wrapper mode
except ImportError:
    import patch_mtkbt  # standalone mode

# Override map: offset → (expected current "after" byte, new "after" byte, label suffix)
OVERRIDES = {
    # B1/B2/B3 — AVCTP 1.3 → 1.4
    0x0eba6d: (0x03, 0x04, "AVCTP 1.3->1.4 (Pixel-shape)"),
    0x0eba37: (0x03, 0x04, "AVCTP 1.3->1.4 (Pixel-shape)"),
    0x0eba25: (0x03, 0x04, "AVCTP 1.3->1.4 (Pixel-shape)"),
    # C1/C2/C3 — AVRCP 1.4 → 1.5
    0x0eba4b: (0x04, 0x05, "AVRCP 1.4->1.5 (Pixel-shape)"),
    0x0eba58: (0x04, 0x05, "AVRCP 1.4->1.5 (Pixel-shape)"),
    0x0eba77: (0x04, 0x05, "AVRCP 1.4->1.5 (Pixel-shape)"),
    # E3/E4 — SupportedFeatures 0x33 → 0xd1
    0x0eba5b: (0x33, 0xd1, "SupportedFeatures 0x0033->0x00d1 (Pixel-shape)"),
    0x0eba4e: (0x33, 0xd1, "SupportedFeatures 0x0033->0x00d1 (Pixel-shape)"),
}

def _override_pixel_shape(patches):
    found = {off: False for off in OVERRIDES}
    for p in patches:
        if p["offset"] in OVERRIDES:
            expected_old, new_byte, label = OVERRIDES[p["offset"]]
            if p["after"] != bytes([expected_old]):
                raise RuntimeError(
                    f"Patch at 0x{p['offset']:06x} 'after'={p['after'].hex()} "
                    f"expected 0x{expected_old:02x}; patch_mtkbt.py may have changed."
                )
            p["after"] = bytes([new_byte])
            p["name"] = p["name"] + f"  [PIXEL-SHAPE: {label}]"
            found[p["offset"]] = True
    missing = [hex(o) for o, ok in found.items() if not ok]
    if missing:
        raise RuntimeError(f"Override targets not found at: {missing}")

def main():
    print("=== EXPERIMENT: SDP record shape matched to Pixel 4 ===")
    print("    AVCTP 1.3 -> 1.4 (B1/B2/B3)")
    print("    AVRCP 1.4 -> 1.5 (C1/C2/C3)")
    print("    SupportedFeatures 0x0033 -> 0x00d1 (E3/E4)")
    print("    Reverting = re-run normal --avrcp --bluetooth flow")
    print()
    _override_pixel_shape(patch_mtkbt.PATCHES)
    patch_mtkbt.main()

if __name__ == "__main__":
    main()
