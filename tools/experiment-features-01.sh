#!/usr/bin/env bash
# experiment-features-01.sh — re-flash with SupportedFeatures 0x33 -> 0x01
# (Cat1 only). Matches the features bitmask Pixel 4 advertises at AVRCP 1.3.
#
# Hypothesis (Test E in the active investigation): Y1's bug isn't the version
# inflation per se, it's that we *also* claim feature bits whose handlers
# mtkbt doesn't have. Trimming features back to the minimum (Cat1) at our
# current AVRCP 1.4 advertisement may let Sonos engage with whatever minimal
# 1.3-class command handling mtkbt actually has.
#
# What this script does mechanically:
#   1. Renames src/patches/patch_mtkbt.py -> patch_mtkbt_orig.py.
#   2. Drops tools/patch_mtkbt_features_01.py into src/patches/patch_mtkbt.py.
#   3. Invokes the normal bash with --avrcp --bluetooth and any extra flags
#      you pass.
#   4. ALWAYS restores patch_mtkbt.py and removes patch_mtkbt_orig.py
#      (trap on EXIT).
#
# Pre-req: src/Y1MediaBridge/app/build/outputs/apk/debug/app-debug.apk must
# exist (--avrcp installs it). If you've never built it on this machine:
#   ( cd src/Y1MediaBridge && ./gradlew --stop && ./gradlew assembleDebug )
#
# Usage:
#   ./tools/experiment-features-01.sh --artifacts-dir ~/y1-patches
#   ./tools/experiment-features-01.sh --artifacts-dir ~/y1-patches --all
#
# Reverting the experiment is a normal re-flash:
#   ./innioasis-y1-fixes.bash --artifacts-dir ~/y1-patches --avrcp --bluetooth

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORIG="$REPO/src/patches/patch_mtkbt.py"
ORIG_RENAMED="$REPO/src/patches/patch_mtkbt_orig.py"
EXP="$REPO/tools/patch_mtkbt_features_01.py"

if [ ! -f "$ORIG" ]; then echo "ERROR: $ORIG missing"; exit 1; fi
if [ ! -f "$EXP"  ]; then echo "ERROR: $EXP missing";  exit 1; fi
if [ -f "$ORIG_RENAMED" ]; then
    echo "ERROR: $ORIG_RENAMED already exists - a previous experiment was interrupted."
    echo "       Inspect, then either delete it or restore patch_mtkbt.py before retrying."
    exit 1
fi

cleanup() {
    if [ -f "$ORIG_RENAMED" ]; then
        cp "$ORIG_RENAMED" "$ORIG"
        rm -f "$ORIG_RENAMED"
        echo
        echo "[restored] $ORIG (from patch_mtkbt_orig.py)"
    fi
}
trap cleanup EXIT

mv "$ORIG" "$ORIG_RENAMED"
cp "$EXP"  "$ORIG"
echo "[swapped] $ORIG <- tools/patch_mtkbt_features_01.py  (E3/E4 0x33 -> 0x01)"
echo "[stashed] $ORIG_RENAMED <- original patch_mtkbt.py"
echo

"$REPO/innioasis-y1-fixes.bash" --avrcp --bluetooth "$@"
