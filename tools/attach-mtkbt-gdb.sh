#!/usr/bin/env bash
# attach-mtkbt-gdb.sh — attach gdbserver to the live mtkbt daemon for
# breakpoint-driven RE of the AVCTP-RX classifier.
#
# Pre-reqs:
#   - --root flashed (su needs to work via `adb shell su -c ...`)
#   - gdbserver binary on the host at one of:
#         tools/gdbserver  (preferred — in-tree)
#         $GDBSERVER       (env var override)
#         $ANDROID_NDK_HOME/prebuilt/android-arm/gdbserver/gdbserver
#     Must be ARM 32-bit, statically linked, API 17 / Android 4.2 compatible.
#     NDK r10e ships a working build at $NDK/prebuilt/android-arm/gdbserver/.
#     Or pull from AOSP prebuilts/misc/android-arm/gdbserver/.
#
# Host side: gdb-multiarch (Debian/Ubuntu) or arm-linux-gnu-gdb (Fedora).
#
# What this does:
#   1. Validates gdbserver and adb device.
#   2. Finds live mtkbt PID + reads /proc/<pid>/maps to get the PIE base.
#   3. Pushes gdbserver to /data/local/tmp/.
#   4. Starts `gdbserver --attach :<port> <pid>` on the device under su.
#   5. Sets up `adb forward tcp:<port> tcp:<port>`.
#   6. Generates a gdb command file with breakpoints at the AVCTP-RX
#      classifier sites (file offsets 0x6db7c / 0x6dc36 / 0x6dc52) + the
#      msg-id-emit dispatcher arms (0x515ca / 0x51622), translated to
#      live addresses using the captured PIE base.
#   7. Prints the one-line command for the user to launch gdb against the
#      generated command file.
#
# Usage:
#   ./tools/attach-mtkbt-gdb.sh                       # default flow
#   ./tools/attach-mtkbt-gdb.sh --port 5039           # alternate port
#   ./tools/attach-mtkbt-gdb.sh --gdbserver /path/to/gdbserver
#
# Driving the capture (once gdb is running):
#   1. Drive a Sonos session: BT toggle on Y1 → Sonos reconnect → expect
#      one inbound VENDOR_DEPENDENT GetCapabilities frame.
#   2. Press pause on Sonos → expect one inbound PASSTHROUGH frame.
#   3. The gdb command file's `commands` blocks log register + memory state
#      at each BP and continue automatically.
#   4. Compare the captured `[r5+5]` values, AV/C op_code bytes, and which
#      branch each frame takes. That settles which patch site is real.
#
# Watch-items:
#   - Each BP halt freezes mtkbt's RX thread for as long as the BP commands
#     run. Sonos times out after a few seconds. Keep `commands` blocks short
#     (silent + printf + continue) so peers don't disconnect mid-capture.
#   - After detach, mtkbt may be in a wedged state. `BT off → on` resets it.

set -u

show_help() {
    cat <<'EOF'
Usage: ./tools/attach-mtkbt-gdb.sh [--port N] [--gdbserver PATH]

Attach gdbserver to the live mtkbt daemon and prepare a gdb command file
for breakpoint-driven RE of the AVCTP-RX classifier.

Options:
    --port N            TCP port for the gdbserver tunnel (default 5039)
    --gdbserver PATH    Override gdbserver binary path

The script doesn't launch gdb itself — it sets up the device side and
prints the command to invoke gdb against the generated command file.

Pre-reqs: --root flashed. gdbserver ARM 32-bit static binary at
tools/gdbserver, $GDBSERVER, or under $ANDROID_NDK_HOME/prebuilt/.
EOF
}

PORT=5039
GDBSERVER=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)       PORT="$2"; shift 2 ;;
        --gdbserver)  GDBSERVER="$2"; shift 2 ;;
        -h|--help)    show_help; exit 0 ;;
        *)            echo "ERROR: unknown option '$1'" >&2; show_help >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Locate gdbserver: explicit flag > env var > tools/gdbserver > NDK prebuilt
if [ -z "$GDBSERVER" ]; then
    GDBSERVER="${GDBSERVER:-${ENV_GDBSERVER:-}}"
fi
if [ -z "$GDBSERVER" ] && [ -n "${GDBSERVER:-}" ]; then
    :
fi
for candidate in \
        "${GDBSERVER:-}" \
        "${SCRIPT_DIR}/gdbserver" \
        "${ANDROID_NDK_HOME:-/dev/null}/prebuilt/android-arm/gdbserver/gdbserver" \
        ; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        GDBSERVER="$candidate"; break
    fi
done

if [ -z "$GDBSERVER" ] || [ ! -x "$GDBSERVER" ]; then
    cat >&2 <<'EOF'
ERROR: gdbserver not found.

Easiest fix:
  ./tools/install-gdbserver.sh

Or manually place an ARM 32-bit, statically-linked, API-17-compatible
gdbserver at one of:
  - tools/gdbserver  (in-tree, preferred)
  - $ANDROID_NDK_HOME/prebuilt/android-arm/gdbserver/gdbserver
  - explicit path via --gdbserver <path> or $GDBSERVER env var

Verify it's the right shape:
  file /path/to/gdbserver
  # → ELF 32-bit LSB executable, ARM, EABI5, statically linked
EOF
    exit 1
fi

# Validate adb
if ! adb get-state >/dev/null 2>&1; then
    echo "ERROR: no device. Connect the Y1 and retry." >&2
    exit 1
fi

# Validate gdbserver binary architecture
gdb_file=$(file "$GDBSERVER")
if ! echo "$gdb_file" | grep -q 'ARM.*statically linked'; then
    echo "WARNING: $GDBSERVER may not be ARM/static. file says: $gdb_file" >&2
fi

echo "==> Discovering mtkbt PID + PIE base.."

# Stock toybox on Y1 lacks pidof/head/awk — do parsing host-side from
# `adb shell ps` and `cat /proc/<pid>/maps` (the latter under su).
ps_out=$(adb shell 'ps' 2>/dev/null | tr -d '\r')
MTKBT_PID=""
while IFS= read -r row; do
    case "$row" in
        *' /system/bin/mtkbt'|*' mtkbt')
            # toybox ps: USER PID PPID VSIZE RSS WCHAN PC NAME
            set -- $row
            MTKBT_PID=$2
            break
            ;;
    esac
done <<< "$ps_out"

if [ -z "$MTKBT_PID" ]; then
    echo "ERROR: mtkbt not running. BT enabled? Check 'adb shell ps | grep mtkbt'." >&2
    exit 1
fi

maps_out=$(adb shell "su -c 'cat /proc/${MTKBT_PID}/maps'" 2>/dev/null | tr -d '\r')
PIE_BASE_HEX=""
while IFS= read -r row; do
    case "$row" in
        *'/system/bin/mtkbt')
            # maps line format: <start>-<end> rwxp <off> <dev> <inode>  <path>
            range=${row%% *}
            PIE_BASE_HEX=${range%%-*}
            break
            ;;
    esac
done <<< "$maps_out"

if [ -z "$PIE_BASE_HEX" ]; then
    echo "ERROR: couldn't read /proc/${MTKBT_PID}/maps. su working?" >&2
    exit 1
fi
PIE_BASE=$((16#$PIE_BASE_HEX))

printf "    mtkbt pid=%s  PIE base=0x%x\n" "$MTKBT_PID" "$PIE_BASE"

# Compute live addresses (file_offset + PIE_base, ORed with 1 for Thumb mode)
fileoff_to_live() {
    local off=$1
    printf "0x%x" $(( off + PIE_BASE ))
}

BP_6da7a=$(fileoff_to_live 0x6da7a)   # inner TBH dispatcher (event subtype byte)
BP_6db7c=$(fileoff_to_live 0x6db7c)   # classifier — ldrb [r5,#5]; cmp #1; bhi
BP_6dc36=$(fileoff_to_live 0x6dc36)   # event_code=4 setter (AV/C parse path)
BP_6dc52=$(fileoff_to_live 0x6dc52)   # event_code=8 setter (raw-forward path)
BP_515ca=$(fileoff_to_live 0x515ca)   # dispatcher case 3 (event 4 → msg 506)
BP_51622=$(fileoff_to_live 0x51622)   # dispatcher case 7 (event 8 → msg 519)

echo "==> Pushing gdbserver to /data/local/tmp/.."
adb push "$GDBSERVER" /data/local/tmp/gdbserver >/dev/null
adb shell 'su -c "chmod 755 /data/local/tmp/gdbserver"'

echo "==> Setting up adb forward localhost:${PORT} → device:${PORT}.."
adb forward "tcp:${PORT}" "tcp:${PORT}"

GDB_CMDS="${REPO_ROOT}/tools/_attach-mtkbt-gdb-commands.gdb"
cat > "$GDB_CMDS" <<EOF
# Auto-generated by tools/attach-mtkbt-gdb.sh — do not edit; regenerate.
# PIE base: ${PIE_BASE_HEX}, mtkbt PID: ${MTKBT_PID}, port: ${PORT}.

set pagination off
set confirm off
set print pretty on
set logging file /tmp/mtkbt-gdb.log
set logging overwrite on
set logging on

target remote :${PORT}

# AVCTP-RX inner TBH dispatcher (file 0x6da7a) — reads byte at [r5,#0]
break *${BP_6da7a}
commands
silent
printf "BP@0x6da7a (TBH key): r1=%u r4=0x%x r5=0x%x\n", \$r1, \$r4, \$r5
printf "  [r5+0]=%u (event subtype) [r5+5]=%u  payload@[r5+16]:\n", *(unsigned char*)(\$r5+0), *(unsigned char*)(\$r5+5)
x/16xb *(unsigned int*)(\$r5+16)
continue
end

# AVCTP-RX classifier (file 0x6db7c) — branches on [r5+5] > 1
break *${BP_6db7c}
commands
silent
printf "BP@0x6db7c (classifier): [r5+5]=%u (gate: >1 → event_code 8, ≤1 → AV/C parse)\n", *(unsigned char*)(\$r5+5)
printf "  AV/C-style decode: ctype=0x%x subunit=0x%x op_code=0x%x\n", *(unsigned char*)(*(unsigned int*)(\$r5+16)+0), *(unsigned char*)(*(unsigned int*)(\$r5+16)+1), *(unsigned char*)(*(unsigned int*)(\$r5+16)+2)
continue
end

# event_code=4 setter (AV/C parse path → msg 506 CONNECT_IND)
break *${BP_6dc36}
commands
silent
printf "BP@0x6dc36 → event_code=4 (msg 506 CONNECT_IND path)\n"
continue
end

# event_code=8 setter (raw-forward path → msg 519 CMD_FRAME_IND)
break *${BP_6dc52}
commands
silent
printf "BP@0x6dc52 → event_code=8 (msg 519 CMD_FRAME_IND path)\n"
continue
end

# Dispatcher arm for event 4 → 0x512e8 → msg 506
break *${BP_515ca}
commands
silent
printf "BP@0x515ca: dispatcher firing case 3 (event 4 → msg 506 CONNECT_IND)\n"
continue
end

# Dispatcher arm for event 8 → 0x51410 → msg 519
break *${BP_51622}
commands
silent
printf "BP@0x51622: dispatcher firing case 7 (event 8 → msg 519 CMD_FRAME_IND)\n"
continue
end

continue
EOF

echo "==> gdb command file written to ${GDB_CMDS}"
echo "==> Starting gdbserver --attach :${PORT} ${MTKBT_PID} on device.."
echo "    (Ctrl-C this script when done; it will leave gdbserver on the device"
echo "     for the gdb session to drive.  When you exit gdb, gdbserver dies"
echo "     too and mtkbt resumes.)"
echo
echo "In a SECOND terminal, run:"

# Try to identify the host gdb command
HOST_GDB=""
for c in gdb-multiarch arm-linux-gnu-gdb arm-linux-gnueabi-gdb gdb; do
    if command -v "$c" >/dev/null 2>&1; then HOST_GDB="$c"; break; fi
done

if [ -n "$HOST_GDB" ]; then
    echo "    ${HOST_GDB} -x ${GDB_CMDS} ${REPO_ROOT}/staging/system_extracted/system/bin/mtkbt"
    echo "    # (or whatever stock-mtkbt path you have; symbols help but aren't required)"
else
    echo "    gdb-multiarch -x ${GDB_CMDS} <path-to-stock-mtkbt>"
    echo "    # gdb-multiarch / arm-linux-gnu-gdb not detected on this host —"
    echo "    # install one to get nicer disassembly + symbol resolution."
fi

echo
echo "Then drive the Sonos scenario:"
echo "    1. BT off → on on the Y1, let Sonos reconnect"
echo "    2. Look for 'BP@0x6db7c' line — should fire once for VENDOR_DEPENDENT"
echo "       GetCapabilities (14B payload) shortly after AVCTP connect."
echo "    3. Press pause on Sonos — should fire BP@0x6db7c again (8B PASSTHROUGH)."
echo "    4. Compare [r5+5] values + payload byte 2 (AV/C op_code) between the two."
echo "    Output is logged to /tmp/mtkbt-gdb.log."
echo

# Run gdbserver in foreground; ctrl-C kills it (and mtkbt's ptrace slot is freed
# when gdb on the host detaches). The gdbserver process exits when the gdb
# client disconnects.
adb shell "su -c '/data/local/tmp/gdbserver --attach :${PORT} ${MTKBT_PID}'"
