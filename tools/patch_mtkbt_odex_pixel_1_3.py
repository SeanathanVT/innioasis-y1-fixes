#!/usr/bin/env python3
"""
patch_mtkbt_odex_pixel_1_3.py — EXPERIMENT only. Wraps
src/patches/patch_mtkbt_odex.py and reverts F1 to stock so getPreferVersion()
returns 10 (= AVRCP 1.3 in BlueAngel's enum) instead of 14 (= AVRCP 1.4).

Paired with tools/patch_mtkbt_pixel_1_3.py to make the entire patch chain
(daemon + Java) coherent at AVRCP 1.3, mimicking what Pixel 4 advertises at
its dev-options-forced 1.3 mode.

Per the brief: BlueAngel's version enum maps 10 -> 1.3, 14 -> 1.4. Stock
returned 10 (1.3); standard --avrcp F1 patches to 14 (1.4). Test F reverts
to stock 10 so the JNI activate request goes out as version 1.3.

This script is invocation-compatible with patch_mtkbt_odex.py:
    python3 patch_mtkbt_odex_pixel_1_3.py <stock_odex> --output <patched_odex>
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
    import patch_mtkbt_odex_orig as patch_mtkbt_odex  # bash-wrapper mode
except ImportError:
    import patch_mtkbt_odex  # standalone mode

# F1 — getPreferVersion() return value
# Standard: stock 0x0a (10 = AVRCP 1.3) -> patched 0x0e (14 = AVRCP 1.4)
# Test F:   override 'after' back to 0x0a (effectively don't change from stock)
F1_OFFSET = 0x3e0ea
F1_EXPECTED_AFTER = bytes([0x0e])
F1_NEW_AFTER      = bytes([0x0a])

def _override_pixel_1_3(patches):
    f1_found = False
    for p in patches:
        if p["offset"] == F1_OFFSET:
            if p["after"] != F1_EXPECTED_AFTER:
                raise RuntimeError(
                    f"F1 patch at 0x{F1_OFFSET:06x} 'after'={p['after'].hex()} "
                    f"expected {F1_EXPECTED_AFTER.hex()}; patch_mtkbt_odex.py may have changed."
                )
            p["after"] = F1_NEW_AFTER
            p["name"] = p["name"] + "  [TEST-F: revert getPreferVersion 14 -> 10 (AVRCP 1.3)]"
            f1_found = True
            break
    if not f1_found:
        raise RuntimeError(f"F1 override target 0x{F1_OFFSET:06x} not found in PATCHES")

def main():
    print("=== EXPERIMENT (Test F): MtkBt.odex F1 reverted (1.4 -> 1.3) ===")
    print("    getPreferVersion() returns 10 (AVRCP 1.3) instead of 14 (1.4).")
    print()
    _override_pixel_1_3(patch_mtkbt_odex.PATCHES)
    patch_mtkbt_odex.main()

if __name__ == "__main__":
    main()
