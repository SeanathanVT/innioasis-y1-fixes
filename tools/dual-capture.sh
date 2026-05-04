#!/usr/bin/env bash
# dual-capture — capture mtkbt's @btlog stream AND logcat simultaneously,
# with per-line timestamps in both so they can be correlated post-hoc.
#
# Output layout:
#   <out_dir>/btlog.bin        — raw @btlog stream (parse with tools/btlog-parse.py)
#   <out_dir>/logcat.txt       — `logcat -v threadtime` against -b main -b system -b radio
#                                (Android 4.2.2 doesn't support `-b all`)
#   <out_dir>/dmesg-before.txt — kernel ring buffer at start
#   <out_dir>/dmesg-after.txt  — kernel ring buffer at stop
#   <out_dir>/getprop.txt      — getprop snapshot
#
# Usage:
#   ./tools/dual-capture.sh                              # interactive — Ctrl-C to stop
#   ./tools/dual-capture.sh <out_dir>                    # custom output dir
#
# Default <out_dir>: /tmp/koensayr-dual-<UTC-timestamp>/
#
# While capturing: drive the AVRCP scenario on the device (toggle BT off/on,
# pair/connect, change tracks, etc.). Pre-req: --root flashed.

set -u

OUT="${1:-/tmp/koensayr-dual-$(date -u +%Y%m%dT%H%M%SZ)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOL_BIN="$REPO_ROOT/src/btlog-dump/build/btlog-dump"

if [ ! -x "$TOOL_BIN" ]; then
    echo "Building btlog-dump..."
    ( cd "$REPO_ROOT/src/btlog-dump" && make ) || { echo "build failed"; exit 1; }
fi

if ! adb get-state >/dev/null 2>&1; then
    echo "ERROR: no device" >&2
    exit 1
fi

mkdir -p "$OUT"
echo "Output dir: $OUT"

# Snapshot kernel state + props
adb shell 'su -c dmesg' > "$OUT/dmesg-before.txt" 2>&1
adb shell getprop > "$OUT/getprop.txt" 2>&1

# Push the dumper
adb push "$TOOL_BIN" /data/local/tmp/btlog-dump >/dev/null
adb shell 'chmod 755 /data/local/tmp/btlog-dump'

# Clean logcat buffers so we capture only this session.
# Android 4.2.2 doesn't support `-b all` (no /dev/log/all); list buffers explicitly.
# `-b main` is the default (BT framework + AVRCP tags land here); add system/radio
# for completeness. `events` is binary-only, skip it.
adb logcat -b main -b system -b radio -c 2>/dev/null

echo
echo "Starting dual capture. Run the AVRCP scenario now."
echo "When done, press Ctrl-C to stop."
echo

# Start logcat in background. -v threadtime → per-line timestamps for cross-stream
# correlation. Tag filter: AVRCP-related tags + Bluetooth framework + catch-all
# silenced by '*:S'.
adb logcat -v threadtime -b main -b system -b radio \
    DebugY1:V Y1MediaBridge:V MMI_AVRCP:V JNI_AVRCP:V EXT_AVRCP:V \
    BWS_AVRCP:V EXTADP_AVRCP:V \
    BluetoothAvrcpService:V BluetoothAvrcpServiceJni:V \
    Bluetooth:V BluetoothManagerService:V BluetoothAdapterService:V \
    bt_btif:V bt_hci:V mtkbt:V \
    '*:S' > "$OUT/logcat.txt" 2>&1 &
LOGCAT_PID=$!

# Start btlog capture. The remote `su -c /path/to/btlog-dump` runs under the
# adb-shell session; killing the local adb child closes the remote shell which
# kills the chain. We do NOT use `pkill` for cleanup — it's missing on the
# Y1's stock toolbox. If the remote process leaks, it dies on next BT toggle
# or reboot (both small impacts).
adb shell 'su -c /data/local/tmp/btlog-dump' > "$OUT/btlog.bin" 2>"$OUT/btlog.err" &
BTLOG_PID=$!

trap '
echo
echo "-- stopping..."
kill $LOGCAT_PID 2>/dev/null
kill $BTLOG_PID  2>/dev/null
sleep 1
# Kill any lingering on-device btlog-dump via /proc walk (no pkill on device).
# Pure shell, no external utils.
adb shell "su -c \"for d in /proc/[0-9]*; do n=\\\$(cat \\\$d/comm 2>/dev/null); if [ \\\"\\\$n\\\" = btlog-dump ]; then kill \\\${d#/proc/} 2>/dev/null; fi; done\"" 2>/dev/null
wait 2>/dev/null
adb shell "su -c dmesg" > "$OUT/dmesg-after.txt" 2>&1
echo
echo "Captured to: $OUT"
echo "  btlog.bin:  $(wc -c < "$OUT/btlog.bin"   2>/dev/null || echo 0) bytes"
echo "  logcat.txt: $(wc -l < "$OUT/logcat.txt" 2>/dev/null || echo 0) lines"
echo
echo "Quick decode:"
echo "  ./tools/btlog-parse.py \"$OUT/btlog.bin\" --tag-include AVRCP --tag-include AVCTP --tag-exclude \"GetByte\" --tag-exclude \"PutByte\""
echo "  grep -E \"CONNECT_CNF|activeVersion|REGISTER_NOTIFICATION|tg_feature\" \"$OUT/logcat.txt\""
exit 0
' INT TERM

wait
