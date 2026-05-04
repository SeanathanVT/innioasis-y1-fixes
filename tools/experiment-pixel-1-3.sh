#!/usr/bin/env bash
# experiment-pixel-1-3.sh — re-flash with mtkbt + MtkBt.odex shaped to mimic
# Pixel 4's AVRCP TG record at AVRCP 1.3 (verified working with Sonos via
# user test 2026-05-04: title/artist/album + play/pause both work).
#
# Hypothesis (Test F in the active investigation): mtkbt may have latent
# AVRCP 1.3 command handlers that activate when we advertise 1.3 with a
# coherent shape. The brief's standard --avrcp patches inflate version to
# 1.4; if mtkbt is internally a 1.3-class implementation that we've been
# mis-advertising as 1.4, dropping back to 1.3 with Pixel-1.3-shape features
# may unstick the AVRCP COMMAND path.
#
# Concretely (changes vs. standard --avrcp):
#   B1/B2/B3  AVCTP 1.3 -> 1.2
#   C1/C2/C3  AVRCP 1.4 -> 1.3
#   E3/E4     SupportedFeatures 0x33 -> 0x01 (Cat1 only, matches Pixel-1.3)
#   A1        runtime MOVW r7 immediate: 0x0401 -> 0x0301 (1.4 -> 1.3)
#   F1 (odex) getPreferVersion() return: 14 -> 10 (AVRCP 1.4 -> 1.3)
#
# All other patches (D1, E8, F2, C2a/b, C3a/b, C4) and the Y1MediaBridge
# install (still part of --avrcp) are unchanged.
#
# What this script does mechanically:
#   1. Renames src/patches/patch_mtkbt.py      -> patch_mtkbt_orig.py
#   2. Renames src/patches/patch_mtkbt_odex.py -> patch_mtkbt_odex_orig.py
#   3. Drops the experiment patchers in their place (which import the *_orig
#      modules and override targeted entries by offset).
#   4. Invokes the normal bash with --avrcp --bluetooth and any extra flags.
#   5. ALWAYS restores both patcher files (trap on EXIT) so the repo is clean
#      whether the flash succeeds, fails, or is interrupted.
#
# Pre-req: src/Y1MediaBridge/app/build/outputs/apk/debug/app-debug.apk must
# exist (--avrcp installs it). If you've never built it on this machine:
#   ( cd src/Y1MediaBridge && ./gradlew --stop && ./gradlew assembleDebug )
#
# Usage:
#   ./tools/experiment-pixel-1-3.sh --artifacts-dir ~/y1-patches
#   ./tools/experiment-pixel-1-3.sh --artifacts-dir ~/y1-patches --all
#
# Reverting after a flash is a normal --avrcp --bluetooth re-flash:
#   ./innioasis-y1-fixes.bash --artifacts-dir ~/y1-patches --avrcp --bluetooth

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ORIG_MTKBT="$REPO/src/patches/patch_mtkbt.py"
ORIG_MTKBT_RENAMED="$REPO/src/patches/patch_mtkbt_orig.py"
EXP_MTKBT="$REPO/tools/patch_mtkbt_pixel_1_3.py"

ORIG_ODEX="$REPO/src/patches/patch_mtkbt_odex.py"
ORIG_ODEX_RENAMED="$REPO/src/patches/patch_mtkbt_odex_orig.py"
EXP_ODEX="$REPO/tools/patch_mtkbt_odex_pixel_1_3.py"

for f in "$ORIG_MTKBT" "$ORIG_ODEX" "$EXP_MTKBT" "$EXP_ODEX"; do
    if [ ! -f "$f" ]; then echo "ERROR: $f missing"; exit 1; fi
done
for f in "$ORIG_MTKBT_RENAMED" "$ORIG_ODEX_RENAMED"; do
    if [ -f "$f" ]; then
        echo "ERROR: $f already exists - a previous experiment was interrupted."
        echo "       Inspect, then either delete it or restore patch_mtkbt[_odex].py before retrying."
        exit 1
    fi
done

cleanup() {
    if [ -f "$ORIG_MTKBT_RENAMED" ]; then
        cp "$ORIG_MTKBT_RENAMED" "$ORIG_MTKBT"
        rm -f "$ORIG_MTKBT_RENAMED"
        echo "[restored] $ORIG_MTKBT (from patch_mtkbt_orig.py)"
    fi
    if [ -f "$ORIG_ODEX_RENAMED" ]; then
        cp "$ORIG_ODEX_RENAMED" "$ORIG_ODEX"
        rm -f "$ORIG_ODEX_RENAMED"
        echo "[restored] $ORIG_ODEX (from patch_mtkbt_odex_orig.py)"
    fi
}
trap cleanup EXIT

mv "$ORIG_MTKBT" "$ORIG_MTKBT_RENAMED"
cp "$EXP_MTKBT"  "$ORIG_MTKBT"
echo "[swapped] $ORIG_MTKBT <- tools/patch_mtkbt_pixel_1_3.py"
echo "          (B1-B3 AVCTP->1.2, C1-C3 AVRCP->1.3, E3/E4 0x01, A1 reverted)"

mv "$ORIG_ODEX" "$ORIG_ODEX_RENAMED"
cp "$EXP_ODEX"  "$ORIG_ODEX"
echo "[swapped] $ORIG_ODEX <- tools/patch_mtkbt_odex_pixel_1_3.py"
echo "          (F1 reverted: getPreferVersion 14 -> 10 / AVRCP 1.3)"
echo

"$REPO/innioasis-y1-fixes.bash" --avrcp --bluetooth "$@"
