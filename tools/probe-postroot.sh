#!/usr/bin/env bash
# Y1 post-root probe pack v2 — Trace #8 follow-up.
#
# v1 hit two problems on stock Y1 Android 4.2.2:
#   - toybox is missing pidof/head/tail (ENOSYS-equivalent)
#   - `adb shell "su -c '...'"` mangled multi-pipe commands
# v2 fix: push a single self-contained on-device script and exec it as root.
# All probe logic lives in tools/probe-postroot-device.sh.
#
# Usage:
#   ./tools/probe-postroot.sh                   # runs everything
#   ./tools/probe-postroot.sh > probe.log 2>&1  # capture for archival

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEVICE_SCRIPT="$SCRIPT_DIR/probe-postroot-device.sh"

if ! adb get-state >/dev/null 2>&1; then
    echo "ERROR: no device. Connect the Y1 and retry." >&2
    exit 1
fi

if [ ! -f "$DEVICE_SCRIPT" ]; then
    echo "ERROR: $DEVICE_SCRIPT not found." >&2
    exit 1
fi

adb push "$DEVICE_SCRIPT" /data/local/tmp/probe.sh >/dev/null
adb shell 'chmod 755 /data/local/tmp/probe.sh'
adb shell 'su -c /data/local/tmp/probe.sh'
