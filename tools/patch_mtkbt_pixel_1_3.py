#!/usr/bin/env python3
"""
patch_mtkbt_pixel_1_3.py — EXPERIMENT only. Wraps src/patches/patch_mtkbt.py
and overrides B1-B3 / C1-C3 / E3-E4 / A1 to make mtkbt's served SDP record a
byte-for-byte mimic of what Pixel 4 advertises at AVRCP 1.3 (verified working
with Sonos via user test 2026-05-04).

Hypothesis under test (Test F in the active investigation): mtkbt may have
latent AVRCP 1.3 command handlers that activate when we advertise 1.3 with
a coherent shape. The brief's previous "version inflation to 1.4" patches
claim a version mtkbt may not actually implement; if mtkbt is internally a
1.3-class implementation that we've been mis-advertising as 1.4, dropping
back to 1.3 with Pixel-1.3-shape features may unstick the AVRCP COMMAND path.

Concretely:
    Site             Standard --avrcp     Test F (Pixel-1.3 mimic)
    --------------   --------------       --------------------------
    B1/B2/B3 AVCTP   0x03 (AVCTP 1.3)     0x02 (AVCTP 1.2)
    C1/C2/C3 AVRCP   0x04 (AVRCP 1.4)     0x03 (AVRCP 1.3)
    E3/E4 features   0x33                 0x01 (Cat1 only)
    A1 MOVW r7       40 f2 01 47 (1.4)    40 f2 01 37 (revert to 1.3)

D1, E8 unchanged (operational, not version-related).

This script is invocation-compatible with patch_mtkbt.py. The wrapper bash
ALSO swaps in tools/patch_mtkbt_odex_pixel_1_3.py to pair with this — see
tools/experiment-pixel-1-3.sh.

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

# Single-byte overrides: offset -> (expected current "after", new "after", label)
SINGLE_BYTE_OVERRIDES = {
    # B1/B2/B3 — AVCTP 1.3 -> 1.2
    0x0eba6d: (0x03, 0x02, "AVCTP 1.3 -> 1.2 (Pixel-1.3 mimic)"),
    0x0eba37: (0x03, 0x02, "AVCTP 1.3 -> 1.2 (Pixel-1.3 mimic)"),
    0x0eba25: (0x03, 0x02, "AVCTP 1.3 -> 1.2 (Pixel-1.3 mimic)"),
    # C1/C2/C3 — AVRCP 1.4 -> 1.3
    0x0eba4b: (0x04, 0x03, "AVRCP 1.4 -> 1.3 (Pixel-1.3 mimic)"),
    0x0eba58: (0x04, 0x03, "AVRCP 1.4 -> 1.3 (Pixel-1.3 mimic)"),
    0x0eba77: (0x04, 0x03, "AVRCP 1.4 -> 1.3 (Pixel-1.3 mimic)"),
    # E3/E4 — SupportedFeatures 0x33 -> 0x01
    0x0eba5b: (0x33, 0x01, "SupportedFeatures 0x0033 -> 0x0001 (Pixel-1.3 mimic)"),
    0x0eba4e: (0x33, 0x01, "SupportedFeatures 0x0033 -> 0x0001 (Pixel-1.3 mimic)"),
}

# Multi-byte override: A1 MOVW r7 immediate. Standard patches stock 1.3
# bytes (40 f2 01 37) -> 1.4 (40 f2 01 47). Test F reverts to stock 1.3.
A1_OFFSET = 0x038BFC
A1_EXPECTED_AFTER = bytes([0x40, 0xf2, 0x01, 0x47])  # current 1.4
A1_NEW_AFTER      = bytes([0x40, 0xf2, 0x01, 0x37])  # back to 1.3

def _override_pixel_1_3(patches):
    found = {off: False for off in SINGLE_BYTE_OVERRIDES}
    a1_found = False
    for p in patches:
        if p["offset"] in SINGLE_BYTE_OVERRIDES:
            expected_old, new_byte, label = SINGLE_BYTE_OVERRIDES[p["offset"]]
            if p["after"] != bytes([expected_old]):
                raise RuntimeError(
                    f"Patch at 0x{p['offset']:06x} 'after'={p['after'].hex()} "
                    f"expected 0x{expected_old:02x}; patch_mtkbt.py may have changed."
                )
            p["after"] = bytes([new_byte])
            p["name"] = p["name"] + f"  [TEST-F: {label}]"
            found[p["offset"]] = True
        elif p["offset"] == A1_OFFSET:
            if p["after"] != A1_EXPECTED_AFTER:
                raise RuntimeError(
                    f"A1 patch at 0x{A1_OFFSET:06x} 'after'={p['after'].hex()} "
                    f"expected {A1_EXPECTED_AFTER.hex()}; patch_mtkbt.py may have changed."
                )
            p["after"] = A1_NEW_AFTER
            p["name"] = p["name"] + "  [TEST-F: revert MOVW r7,#0x0401 -> #0x0301]"
            a1_found = True
    missing = [hex(o) for o, ok in found.items() if not ok]
    if missing or not a1_found:
        raise RuntimeError(
            f"Override targets not all found. single-byte missing: {missing}, "
            f"A1 found: {a1_found}"
        )

def main():
    print("=== EXPERIMENT (Test F): Pixel-1.3 SDP shape mimicry ===")
    print("    AVCTP 1.3 -> 1.2 (B1/B2/B3)")
    print("    AVRCP 1.4 -> 1.3 (C1/C2/C3)")
    print("    SupportedFeatures 0x0033 -> 0x0001 (E3/E4)")
    print("    A1 runtime MOVW: 0x0401 -> 0x0301 (1.4 -> 1.3)")
    print("    F1 in MtkBt.odex handled separately by paired patcher.")
    print("    Reverting = re-run normal --avrcp --bluetooth flow")
    print()
    _override_pixel_1_3(patch_mtkbt.PATCHES)
    patch_mtkbt.main()

if __name__ == "__main__":
    main()
