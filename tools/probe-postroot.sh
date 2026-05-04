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

case "${1:-}" in
    -h|--help)
        cat <<EOF
Usage: ./tools/probe-postroot.sh [> probe.log 2>&1]

One-shot post-root sanity probe. Pushes the device-side script
(probe-postroot-device.sh) to /data/local/tmp/probe.sh and execs
it under su.

Enumerates: mtkbt PID + /proc/<pid>/maps (PIE base + library load
addresses), MTK debug-node accessibility, canonical btsnoop file
paths, BT-related getprop keys, dmesg AVRCP/AVCTP/STP traces,
/dev/stp* permissions, mtkbt strings for snoop/persist.bt knobs,
libbluetooth*.so strings for the same, /proc/<pid>/status
capabilities, gdbserver presence anywhere, SELinux mode, ptrace
policy, and /proc/net/unix for bt.ext.adp.* + @btlog abstract sockets.

Pre-req: --root flashed (script needs su access).
Re-run after any new KNOWN_FIRMWARES entry to confirm the probe
results stay consistent.
EOF
        exit 0
        ;;
esac

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
