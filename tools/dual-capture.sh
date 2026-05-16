#!/usr/bin/env bash
# dual-capture — capture mtkbt @btlog + logcat + getevent + dumpsys input
# simultaneously, with per-line timestamps for post-hoc correlation.
# Pre-req: --root flashed. Run --help for output layout and flags.

set -u

UNFILTERED=0
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            cat <<EOF
Usage: ./tools/dual-capture.sh [--unfiltered] [<out_dir>]

Capture mtkbt's @btlog stream AND logcat simultaneously, with per-line
timestamps in both for post-hoc correlation.

Options:
    --unfiltered   capture every logcat tag (no '*:S' silence). Use this
                   when investigating non-AVRCP behavior — e.g. the Y1
                   music app's 'DebugY1  <Class>' Timber tags, which the
                   default filter cannot match because logcat's tag-arg
                   parser collapses embedded whitespace through adb's
                   shell layer. Output is ~5-10x bigger.

Output (in <out_dir>):
    btlog.bin           — raw @btlog stream (parse with tools/btlog-parse.py)
    logcat.txt          — logcat -v threadtime against -b main -b system -b radio
    getevent.txt        — getevent -lt raw kernel input events with timestamps
                          (KEY_DOWN/KEY_UP/auto-repeat visible at the kernel layer)
    dumpsys-input.txt   — dumpsys input polled every 0.5s for InputDispatcher
                          held-key state and pending events
    dmesg-before.txt    — kernel ring buffer at start
    dmesg-after.txt     — kernel ring buffer at stop
    getprop.txt         — getprop snapshot

Default <out_dir>: /tmp/koensayr-dual-<UTC-timestamp>/

Pre-req: --root flashed (script needs su access for the @btlog socket).
While capturing: drive the AVRCP scenario on the device (toggle BT
off/on, pair/connect, change tracks, etc.). Ctrl-C to stop.
EOF
            exit 0
            ;;
        --unfiltered)
            UNFILTERED=1
            shift
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done
set -- "${ARGS[@]:-}"

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

# Clean buffers; Android 4.2.2 has no `-b all`, list explicitly. Skip
# `events` (binary-only).
adb logcat -b main -b system -b radio -c 2>/dev/null

echo
echo "Starting dual capture. Run the AVRCP scenario now."
echo "When done, press Ctrl-C to stop."
echo

# Default filter: AVRCP / Bluetooth tags + Y1Patch/Y1Bridge/Y1T (debug
# tags from --debug / KOENSAYR_DEBUG=1 builds). --unfiltered drops the
# filter (also needed for the music app's `DebugY1  <Class>` Timber
# tags, whose embedded whitespace adb's shell layer collapses).
if [ "$UNFILTERED" = "1" ]; then
    adb logcat -v threadtime -b main -b system -b radio \
        > "$OUT/logcat.txt" 2>&1 &
else
    adb logcat -v threadtime -b main -b system -b radio \
        Y1Patch:V Y1Bridge:V Y1T:V \
        MMI_AVRCP:V JNI_AVRCP:V EXT_AVRCP:V BWS_AVRCP:V EXTADP_AVRCP:V \
        BluetoothAvrcpService:V BluetoothAvrcpServiceJni:V \
        Bluetooth:V BluetoothManagerService:V BluetoothAdapterService:V \
        bt_btif:V bt_hci:V mtkbt:V \
        '*:S' > "$OUT/logcat.txt" 2>&1 &
fi
LOGCAT_PID=$!

# btlog: killing the local adb child closes the remote shell, killing the
# chain. No pkill on the device toolbox; leaked dumpers die on next BT
# toggle / reboot.
adb shell 'su -c /data/local/tmp/btlog-dump' > "$OUT/btlog.bin" 2>"$OUT/btlog.err" &
BTLOG_PID=$!

# getevent -lt: symbolic-name kernel input events with timestamps —
# ground truth for KEY_DOWN/UP/auto-repeat behaviour.
adb shell 'su -c "getevent -lt"' > "$OUT/getevent.txt" 2>>"$OUT/btlog.err" &
GETEVENT_PID=$!

# Snapshot `dumpsys input` every 500 ms with a UTC prefix for cross-stream
# alignment; surfaces InputDispatcher held-key state + pending queue.
(
    while true; do
        printf '\n===== %s =====\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
        adb shell 'dumpsys input' 2>/dev/null
        sleep 0.5
    done
) > "$OUT/dumpsys-input.txt" 2>>"$OUT/btlog.err" &
DUMPSYS_PID=$!

trap '
echo
echo "-- stopping..."
kill $LOGCAT_PID   2>/dev/null
kill $BTLOG_PID    2>/dev/null
kill $GETEVENT_PID 2>/dev/null
kill $DUMPSYS_PID  2>/dev/null
sleep 1
# /proc walk reap — no pkill on device.
adb shell "su -c \"for d in /proc/[0-9]*; do n=\\\$(cat \\\$d/comm 2>/dev/null); if [ \\\"\\\$n\\\" = btlog-dump ] || [ \\\"\\\$n\\\" = getevent ]; then kill \\\${d#/proc/} 2>/dev/null; fi; done\"" 2>/dev/null
wait 2>/dev/null
adb shell "su -c dmesg" > "$OUT/dmesg-after.txt" 2>&1
echo
echo "Captured to: $OUT"
echo "  btlog.bin:         $(wc -c < "$OUT/btlog.bin"          2>/dev/null || echo 0) bytes"
echo "  logcat.txt:        $(wc -l < "$OUT/logcat.txt"         2>/dev/null || echo 0) lines"
echo "  getevent.txt:      $(wc -l < "$OUT/getevent.txt"       2>/dev/null || echo 0) lines"
echo "  dumpsys-input.txt: $(wc -l < "$OUT/dumpsys-input.txt"  2>/dev/null || echo 0) lines"
echo
echo "Quick decode:"
echo "  ./tools/btlog-parse.py \"$OUT/btlog.bin\" --tag-include AVRCP --tag-include AVCTP --tag-exclude \"GetByte\" --tag-exclude \"PutByte\""
echo "  grep -E \"CONNECT_CNF|activeVersion|REGISTER_NOTIFICATION|tg_feature\" \"$OUT/logcat.txt\""
echo "  grep -E \"KEY_(PLAY|PAUSE|FAST|REWIND|NEXT|PREV)\" \"$OUT/getevent.txt\""
exit 0
' INT TERM

wait
