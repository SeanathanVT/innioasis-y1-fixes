#!/usr/bin/env bash
# attach-mtkbt-gdb-avdtp.sh — attach gdbserver to the live mtkbt daemon
# for breakpoint-driven RE of the AVDTP signal dispatcher.
#
# Goal: find the per-sig_id dispatch site so we can design V5 (the
# sig 0x0c GET_ALL_CAPABILITIES handler that closes GAVDP 1.3 ICS
# Acceptor Table 5 row 9 — see docs/BT-COMPLIANCE.md §9.13).
#
# Static analysis converges slowly because mtkbt's AVDTP layer uses
# dense MOVW/MOVT encoding that defeats string-xref grep. Runtime
# trace from a known parser site (0x50b08) is much faster.
#
# Pre-reqs:
#   - --root flashed (su via `adb shell su -c ...`)
#   - tools/gdbserver present (install-gdbserver.sh)
#   - host: gdb-multiarch or arm-linux-gnu-gdb
#
# What this does:
#   1. Validates gdbserver + adb device
#   2. Reads /proc/<mtkbt-pid>/maps for the PIE base
#   3. Pushes gdbserver, attaches over forward port
#   4. Generates a gdb command file with breakpoints at:
#        - 0x50b08 (AVDTP signal-frame parser entry — confirmed)
#        - 0x50b96 (sig_id store at output struct +8)
#        - 0x50c46 (parser convergence point — last instruction before return)
#        - 0xde26 (one of the parser's external callers — likely the AVDTP
#          RX dispatcher entry; logs the post-parse sig_id and the
#          downstream branch target)
#   5. Each BP logs: PC, LR, key registers, frame bytes
#   6. User drives a peer-side AVDTP session that issues GET_CAPABILITIES
#      (sig 0x02) to seed the dispatch path, then optionally a peer at
#      AVDTP 1.3 issues GET_ALL_CAPABILITIES (sig 0x0c) — but the
#      common case (0x02) is enough to trace the dispatcher
#
# Driving the capture:
#   1. Drive a peer-side stream-establishment: pair Y1 with a peer Sink,
#      let it issue DISCOVER (sig 0x01) and GET_CAPABILITIES (sig 0x02).
#      Capture the gdb trace.
#   2. Look at the LR / branch target at the dispatcher BP.
#   3. With the dispatcher offset confirmed, design V5 — typically a
#      single byte / instruction patch that aliases sig 0x0c → 0x02 in
#      whatever cmp / TBB / fn-ptr-table the dispatcher uses.
#
# Watch-items:
#   - Each BP halt freezes the mtkbt RX thread; peer may time out.
#     Keep `commands` blocks short (silent printf + continue).
#   - mtkbt may wedge after detach — BT off → on resets it.

set -u

show_help() {
    cat <<'EOF'
Usage: ./tools/attach-mtkbt-gdb-avdtp.sh [--port N] [--gdbserver PATH]

Attach gdbserver to mtkbt and prepare an AVDTP-dispatcher-hunt gdb
command file. See script header for full usage.

Options:
    --port N            TCP port (default 5040; offset from 5039 used
                        by attach-mtkbt-gdb.sh so both can run side-by-side)
    --gdbserver PATH    gdbserver binary (default: tools/gdbserver)
    -h, --help          this message
EOF
}

PORT=5040
GDBSERVER_HOST="$(dirname "$0")/gdbserver"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)       PORT="$2"; shift 2 ;;
        --gdbserver)  GDBSERVER_HOST="$2"; shift 2 ;;
        -h|--help)    show_help; exit 0 ;;
        *)            echo "ERROR: unknown arg: $1" >&2; show_help; exit 1 ;;
    esac
done

if [[ ! -f "$GDBSERVER_HOST" ]]; then
    echo "ERROR: gdbserver not found at $GDBSERVER_HOST" >&2
    echo "       Run tools/install-gdbserver.sh first" >&2
    exit 1
fi

if ! adb devices | grep -q "device$"; then
    echo "ERROR: no adb device found" >&2
    exit 1
fi

# Stock toybox on Y1 lacks pidof / head / awk / sed — do parsing host-side
# from `adb shell ps` + `cat /proc/<pid>/maps` (the latter under su). Same
# approach as tools/attach-mtkbt-gdb.sh.
echo "==> Discovering mtkbt PID + PIE base.."
ps_out=$(adb shell 'ps' 2>/dev/null | tr -d '\r')
MTKBT_PID=""
while IFS= read -r row; do
    case "$row" in
        *' /system/bin/mtkbt'|*' mtkbt')
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
echo "    mtkbt PID: $MTKBT_PID"

maps_out=$(adb shell "su -c 'cat /proc/${MTKBT_PID}/maps'" 2>/dev/null | tr -d '\r')
PIE_BASE_HEX=""
while IFS= read -r row; do
    case "$row" in
        *'/system/bin/mtkbt')
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
printf "    PIE base: 0x%x\n" "$PIE_BASE"

fileoff_to_live() {
    printf "0x%x" $((PIE_BASE + 16#${1#0x}))
}

# Breakpoints
BP_50b08=$(fileoff_to_live 0x50b08)   # AVDTP signal-frame parser entry
BP_50b96=$(fileoff_to_live 0x50b96)   # parser stores sig_id at output[8]
BP_50c46=$(fileoff_to_live 0x50c46)   # parser convergence (last insn before return)
BP_de26=$(fileoff_to_live 0xde26)     # external caller of parser — candidate AVDTP RX dispatcher

# Push gdbserver + start
adb push "$GDBSERVER_HOST" /data/local/tmp/gdbserver
adb shell "su -c 'chmod 755 /data/local/tmp/gdbserver'"
adb shell "su -c 'pkill -f gdbserver' 2>/dev/null"
adb shell "su -c '/data/local/tmp/gdbserver --attach :$PORT $MTKBT_PID' &" &
sleep 1
adb forward tcp:$PORT tcp:$PORT

CMDFILE="/tmp/_mtkbt-gdb-avdtp-commands.gdb"
cat > "$CMDFILE" <<EOF
set arm fallback-mode thumb
set arm force-mode thumb
set print pretty on

# AVDTP signal-frame parser entry (file 0x50b08)
break *${BP_50b08}
commands
silent
printf "BP@0x50b08 parser entry: r0=0x%08x [r0]={fmt=%u, ptr=0x%08x}, LR=0x%08x\\n", \$r0, *(unsigned char*)\$r0, *(unsigned*)(\$r0+4), \$lr
continue
end

# Parser stores sig_id at output struct +8 (file 0x50b96)
break *${BP_50b96}
commands
silent
printf "BP@0x50b96 sig_id stored: lr=0x%02x (sig_id) at [r1+8] (r1=0x%08x)\\n", \$lr & 0xff, \$r1
continue
end

# Parser convergence (file 0x50c46) — about to return
break *${BP_50c46}
commands
silent
printf "BP@0x50c46 parser exit: r0=0x%x, output struct sig_id=0x%02x\\n", \$r0, *(unsigned char*)(\$r1+8)
continue
end

# External caller of parser (file 0xde26) — candidate dispatcher entry
# When this hits, the next-instruction LR after the bl 0x50b08 will tell
# us where the dispatcher branches based on the parsed sig_id.
break *${BP_de26}
commands
silent
printf "BP@0xde26 dispatcher candidate caller: r0=0x%08x (struct ptr) — next 8 instrs after parser ret are the dispatch site\\n", \$r0
# stop here so we can step into the dispatch
end

# Also break right AFTER the parser returns (de26+4 = de2a)
break *$(fileoff_to_live 0xde2a)
commands
silent
printf "BP@0xde2a parser-return: r0=%d (parse OK?), [struct+0]=%u (fmt-id), [struct+8]=%u (sig_id)\\n", \$r0, *(unsigned char*)(\$r4+0x11c), *(unsigned char*)(\$r4+0x11c+8)
continue
end

target remote :$PORT
echo Now drive a peer-side AVDTP exchange (e.g. pair Y1 with a peer Sink).
echo Watch for sig_id=2 (GET_CAPABILITIES) traces — note the LR / next BP after parser exit.
echo Resume with: continue
EOF

echo
echo "gdb command file written to: $CMDFILE"
echo
echo "Now run:"
echo "  arm-linux-gnu-gdb -x $CMDFILE   # or: gdb-multiarch -x $CMDFILE"
echo
echo "Drive a peer-side AVDTP session (pair Y1 with a Sink) and capture the trace."
echo "The dispatcher is the function that branches based on output[8] (sig_id) right after the parser returns."
