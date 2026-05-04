#!/usr/bin/env bash
# experiment-pixel-shape.sh — re-flash with mtkbt's served SDP record shaped
# to match the Pixel 4's AVRCP TG record (the working reference confirmed via
# Pixel 4 ↔ Sonos metadata transfer).
#
# Hypothesis (Trace #12, in-progress): Y1's served SDP record is internally
# inconsistent vs. modern AOSP/Pixel — different SupportedFeatures bitmask
# (0x0033 vs Pixel's 0x00d1 — only Cat1+PAS in common), older AVCTP version
# (1.3 vs 1.4), older AVRCP version (1.4 vs 1.5). 1.4-aware CTs see the
# inconsistency and fall back to bare-A2DP without exercising AVRCP COMMANDs.
#
# What this script changes (vs the standard --avrcp flash):
#   B1/B2/B3 — AVCTP 1.3 -> 1.4 (one-byte tweak each, 0x03 -> 0x04)
#   C1/C2/C3 — AVRCP 1.4 -> 1.5 (one-byte tweak each, 0x04 -> 0x05)
#   E3/E4    — SupportedFeatures 0x0033 -> 0x00d1 (one-byte tweak each)
# All other patches (A1, D1, E8, MtkBt.odex F1/F2, libextavrcp_jni C2a/b/C3a/b,
# libextavrcp C4) are applied unchanged from src/patches/. The Y1MediaBridge
# install (still part of --avrcp) is also unchanged.
#
# What this script does mechanically:
#   1. Renames src/patches/patch_mtkbt.py → patch_mtkbt_orig.py so the
#      experiment patcher can still import the real patch list.
#   2. Copies tools/patch_mtkbt_pixel_shape.py into src/patches/patch_mtkbt.py.
#      Bash invokes patch_mtkbt.py → loads our experiment → which imports
#      patch_mtkbt_orig → which exposes PATCHES.
#   3. Invokes the normal bash with --avrcp --bluetooth and any extra flags
#      you pass.
#   4. ALWAYS restores patch_mtkbt.py and removes patch_mtkbt_orig.py
#      (trap on EXIT) so the repo is clean whether the flash succeeds, fails,
#      or is interrupted.
#
# Pre-req: src/Y1MediaBridge/app/build/outputs/apk/debug/app-debug.apk must
# exist (--avrcp installs it). If you've never built it on this machine:
#   ( cd src/Y1MediaBridge && ./gradlew --stop && ./gradlew assembleDebug )
#
# Usage:
#   ./tools/experiment-pixel-shape.sh --artifacts-dir ~/y1-patches
#   ./tools/experiment-pixel-shape.sh --artifacts-dir ~/y1-patches --root
#
# Reverting the experiment is just a normal re-flash:
#   ./innioasis-y1-fixes.bash --artifacts-dir ~/y1-patches --avrcp --bluetooth
#
# Then the test:
#   ./tools/dual-capture.sh ~/dual-pixel-shape-attempt1
#   # toggle BT, connect Sonos / car, watch logcat for cardinality:N > 0
#   # in MMI_AVRCP: ACTION_REG_NOTIFY for notifyChange ... cardinality:N
#   # Also watch for play/pause physically working in the car.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORIG="$REPO/src/patches/patch_mtkbt.py"
ORIG_RENAMED="$REPO/src/patches/patch_mtkbt_orig.py"
EXP="$REPO/tools/patch_mtkbt_pixel_shape.py"

if [ ! -f "$ORIG" ]; then echo "ERROR: $ORIG missing"; exit 1; fi
if [ ! -f "$EXP"  ]; then echo "ERROR: $EXP missing";  exit 1; fi
if [ -f "$ORIG_RENAMED" ]; then
    echo "ERROR: $ORIG_RENAMED already exists — a previous experiment was interrupted."
    echo "       Inspect, then either delete it or restore patch_mtkbt.py from it before retrying."
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
echo "[swapped] $ORIG ← tools/patch_mtkbt_pixel_shape.py"
echo "          (B1/B2/B3 AVCTP 1.3->1.4; C1/C2/C3 AVRCP 1.4->1.5; E3/E4 0x33->0xd1)"
echo "[stashed] $ORIG_RENAMED ← original patch_mtkbt.py"
echo

# --avrcp runs the binary patchers (mtkbt + odex + libextavrcp.so + libextavrcp_jni.so)
# and installs Y1MediaBridge.apk. --bluetooth re-applies the audio.conf/build.prop
# tweaks (idempotent). Extra user flags pass through.
"$REPO/innioasis-y1-fixes.bash" --avrcp --bluetooth "$@"
