#!/usr/bin/env python3
"""
patch_mtkbt_features_01.py — EXPERIMENT only. Wraps src/patches/patch_mtkbt.py
and overrides E3/E4 SupportedFeatures from 0x33 -> 0x01 (Cat1 only).

Hypothesis under test (Test E in the active investigation): Pixel 4 at AVRCP 1.3
advertises features=0x0001 (Cat1 only) and Sonos engages with full metadata +
play/pause. Y1 advertising the SAME features=0x0001 but at the Y1's current
AVRCP 1.4 level may be enough to satisfy Sonos — the version inflation is fine
as long as we don't *also* claim feature bits whose handlers mtkbt doesn't have.

Single-byte change to E3 (Group 2 served) and E4 (Group 1 defense). Everything
else (B1/B2/B3, C1/C2/C3, A1, D1, E8) unchanged. Easiest possible test.

This script is invocation-compatible with patch_mtkbt.py:
    python3 patch_mtkbt_features_01.py <stock_mtkbt> --output <patched_mtkbt>

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

OVERRIDES = {
    # E3/E4 — SupportedFeatures: 0x33 -> 0x01 (Cat1 only, matches Pixel 1.3)
    0x0eba5b: (0x33, 0x01, "SupportedFeatures 0x0033->0x0001 (Pixel-1.3 features)"),
    0x0eba4e: (0x33, 0x01, "SupportedFeatures 0x0033->0x0001 (Pixel-1.3 features)"),
}

def _override_features(patches):
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
            p["name"] = p["name"] + f"  [TEST-E: {label}]"
            found[p["offset"]] = True
    missing = [hex(o) for o, ok in found.items() if not ok]
    if missing:
        raise RuntimeError(f"Override targets not found at: {missing}")

def main():
    print("=== EXPERIMENT (Test E): SupportedFeatures 0x33 -> 0x01 ===")
    print("    AVRCP version stays at 1.4; only the features bitmask trims to Cat1.")
    print("    Reverting = re-run normal --avrcp --bluetooth flow")
    print()
    _override_features(patch_mtkbt.PATCHES)
    patch_mtkbt.main()

if __name__ == "__main__":
    main()
