#!/usr/bin/env bash
# attach-mtkbt-gdb-avdtp.sh — attach gdbserver to the live mtkbt daemon for
# breakpoint-driven RE of the AVDTP signal dispatcher (V5 dispatcher hunt).
#
# Sibling of attach-mtkbt-gdb.sh which targets the AVCTP-RX classifier.
# Goal: find the per-sig_id dispatch site so V5 (the sig 0x0c
# GET_ALL_CAPABILITIES handler that closes GAVDP 1.3 ICS Acceptor Table 5
# row 9 — see docs/BT-COMPLIANCE.md §9.13) can be designed.
#
# Static analysis converges slowly because mtkbt's AVDTP code uses dense
# MOVW/MOVT encoding that defeats string-xref grep. Runtime trace from
# the parser site (0x50b08) is much faster.
#
# Pre-reqs:
#   - --root flashed (su needs to work via `adb shell su -c ...`)
#   - gdbserver binary on the host at one of:
#         tools/gdbserver  (preferred — in-tree)
#         $GDBSERVER       (env var override)
#         $ANDROID_NDK_HOME/prebuilt/android-arm/gdbserver/gdbserver
#     Must be ARM 32-bit, statically linked, API 17 / Android 4.2 compatible.
#
# Host side: gdb-multiarch (Debian/Ubuntu) or arm-linux-gnu-gdb (Fedora).
#
# What this does:
#   1. Validates gdbserver and adb device.
#   2. Finds live mtkbt PID + reads /proc/<pid>/maps to get the PIE base.
#   3. Pushes gdbserver to /data/local/tmp/.
#   4. Starts `gdbserver --attach :<port> <pid>` on the device under su.
#   5. Sets up `adb forward tcp:<port> tcp:<port>`.
#   6. Generates a gdb command file with breakpoints at the AVDTP-RX
#      sites located via static analysis: parser entry (0x50b08), sig_id
#      store (0x50b96), parser exit (0x50c46), and a candidate caller
#      (0xde26 + 0xde2a). Translated to live addresses using PIE base.
#   7. Prints the one-line command for the user to launch gdb against the
#      generated command file.
#
# Usage:
#   ./tools/attach-mtkbt-gdb-avdtp.sh                       # default flow
#   ./tools/attach-mtkbt-gdb-avdtp.sh --gdbserver /path/to/gdbserver
#
# Driving the capture (once gdb is running):
#   1. Pair Y1 with any A2DP Sink (Sonos / Bolt / TV).
#   2. The pairing exchange will issue DISCOVER (sig 0x01) +
#      GET_CAPABILITIES (sig 0x02) — breakpoints will fire.
#   3. Watch BP@0x50c46 (parser exit): note the post-return PC the
#      caller branches to. That branch site is the AVDTP signal
#      dispatcher we need to find for V5.
#   4. The gdb command file's `commands` blocks log register + memory state
#      at each BP and continue automatically.
#
# Watch-items:
#   - Each BP halt freezes mtkbt's RX thread for as long as the BP commands
#     run. Most peer CTs time out after a few seconds. Keep `commands` blocks
#     short (silent + printf + continue) so peers don't disconnect mid-capture.
#   - After detach, mtkbt may be in a wedged state. `BT off → on` resets it.

set -u

show_help() {
    cat <<'EOF'
Usage: ./tools/attach-mtkbt-gdb-avdtp.sh [--port N] [--gdbserver PATH]

Attach gdbserver to the live mtkbt daemon and prepare a gdb command file
for breakpoint-driven RE of the AVDTP signal dispatcher (V5 hunt).

Options:
    --port N            TCP port for the gdbserver tunnel (default 5039)
                        — offset from 5039 used by attach-mtkbt-gdb.sh
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

# Host-side gdb. Check up-front rather than after all the device-side setup —
# nothing useful happens without it. Also lets the user install in parallel
# while the script does the device-side attach.
HOST_GDB=""
for c in gdb-multiarch arm-linux-gnu-gdb arm-linux-gnueabi-gdb gdb; do
    if command -v "$c" >/dev/null 2>&1; then HOST_GDB="$c"; break; fi
done

if [ -z "$HOST_GDB" ]; then
    cat >&2 <<'EOF'
ERROR: no ARM-aware gdb on host. The device-side gdbserver needs a gdb on
       this machine to talk to it. Install one of (per distro):

  Debian/Ubuntu:  sudo apt install gdb-multiarch
  Fedora/RHEL:    sudo dnf install gdb
                  (modern Fedora gdb auto-detects ARM targets)
  Arch:           sudo pacman -S gdb-multiarch

       Then re-run this script.

       Probed binaries: gdb-multiarch arm-linux-gnu-gdb arm-linux-gnueabi-gdb gdb
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

# Compute live addresses (file_offset + PIE_base, plain even address).
# mtkbt is entirely Thumb-2. We DON'T OR the address with 1 here because:
#   - gdb plants a Thumb-aware BKPT via `set arm force-mode thumb` below
#     (this prevents the 4-byte ARM BKPT corruption we hit on the first try).
#   - Bit 0 in the *registered* address breaks gdb's trap-time PC lookup:
#     when the BKPT fires the CPU reports PC = even byte-address, gdb's BP
#     list has odd address, lookup misses, gdb treats the trap as a generic
#     SIGTRAP and the `commands` block never runs.
# Both failure modes verified the hard way 2026-05-05.
fileoff_to_live() {
    local off=$1
    printf "0x%x" $(( off + PIE_BASE ))
}

# 0xaa72c is the AVDTP-layer DISPATCHER for both wire signals (entries
# sb=0..12 → sig_id 1-13) AND internal task messages (sb=14..40, e.g. 0x17
# fired in 2026-05-09-22:18 capture — internal state-machine drive event).
# Same TBH table at 0xaa81e routes both. So this fires for AVDTP wire signals
# IFF a peer actually transmits an AVDTP signal frame during gdb attach.
# Earlier captures showed no wire fires only because no pair attempt happened
# during attach, NOT because the dispatcher is wrong.
#
# Decoded table (file offset 0xaa834 = entry for sig 0x0c → 0xab4de stub):
#   sig 0x01 DISCOVER          → 0xaa870 (full)
#   sig 0x02 GET_CAPABILITIES  → 0xaa924 (full)
#   sig 0x03 SET_CONFIGURATION → 0xab66e (full)
#   sig 0x04 GET_CONFIGURATION → 0xaaaf6 (full)
#   sig 0x05 RECONFIGURE       → 0xaab64 (full)
#   sig 0x06 OPEN              → 0xaac6c (full)
#   sig 0x07 START             → 0xaacde (full)
#   sig 0x08 CLOSE             → 0xab786 (epilogue stub)
#   sig 0x09 SUSPEND           → 0xab786 (epilogue stub)
#   sig 0x0a ABORT             → 0xab008 (full)
#   sig 0x0b SECURITY_CONTROL  → 0xab072 (full)
#   sig 0x0c GET_ALL_CAPABILITIES → 0xab4de (STUB — always BAD_LENGTH)
#   sig 0x0d DELAYREPORT       → 0xab540 (full)
#
# fcn.000b0c30 (different fn) is an AVDTP-layer state-machine driver called
# internally — fires constantly during stream lifecycle but with different
# arg shape than wire frames. Useful as a positive control showing the AVDTP
# state machine is alive, but not the wire-RX dispatch path itself.
BP_aa72c=$(fileoff_to_live 0xaa72c)   # AVDTP dispatcher (wire signals + internal msgs)
BP_aa924=$(fileoff_to_live 0xaa924)   # sig 0x02 GET_CAPABILITIES handler
BP_ab4de=$(fileoff_to_live 0xab4de)   # sig 0x0c GET_ALL_CAPABILITIES STUB
BP_aeb9c=$(fileoff_to_live 0xaeb9c)   # response sender called from sig 0x02
BP_afeec=$(fileoff_to_live 0xafeec)   # AvdtpSigParseConfigCmd (sig 0x03 inner)
BP_afd5c=$(fileoff_to_live 0xafd5c)   # AVDTP capability parser
BP_b0c30=$(fileoff_to_live 0xb0c30)   # AVDTP state-machine driver (positive control)
BP_b0b50=$(fileoff_to_live 0xb0b50)   # AVDTP helper

echo "==> Cleaning up stale gdbserver from any prior run.."
# toybox lacks pkill/killall — walk /proc and SIGKILL any gdbserver. Idempotent
# (no-op if nothing to kill). Necessary because a prior mtkbt crash mid-debug
# can leave gdbserver wedged with the dead PID's ptrace slot, blocking the
# next --attach with "Operation not permitted".
adb shell 'su -c "for d in /proc/[0-9]*; do n=\$(cat \$d/comm 2>/dev/null); if [ \"\$n\" = gdbserver ]; then kill -9 \${d#/proc/} 2>/dev/null; fi; done"' >/dev/null 2>&1
adb forward --remove "tcp:${PORT}" >/dev/null 2>&1 || true

echo "==> Pushing gdbserver to /data/local/tmp/.."
adb push "$GDBSERVER" /data/local/tmp/gdbserver >/dev/null
adb shell 'su -c "chmod 755 /data/local/tmp/gdbserver"'

echo "==> Setting up adb forward localhost:${PORT} → device:${PORT}.."
adb forward "tcp:${PORT}" "tcp:${PORT}"

GDB_CMDS="${REPO_ROOT}/tools/_attach-mtkbt-gdb-avdtp-commands.gdb"
cat > "$GDB_CMDS" <<EOF
# Auto-generated by tools/attach-mtkbt-gdb-avdtp.sh — do not edit; regenerate.
# PIE base: ${PIE_BASE_HEX}, mtkbt PID: ${MTKBT_PID}, port: ${PORT}.

set pagination off
set confirm off
set print pretty on
set logging file /tmp/mtkbt-gdb-avdtp.log
set logging overwrite on
set logging enabled on

# mtkbt is all Thumb-2. force-mode thumb makes gdb plant a 2-byte Thumb BKPT
# at every breakpoint regardless of address parity / symbol info. fallback-mode
# is the looser version (only when gdb can't otherwise decide) — keep both so
# disassembly + BP planting are unambiguous.
set arm fallback-mode thumb
set arm force-mode thumb

target remote :${PORT}

# --- 0xaa72c: AVDTP dispatcher entry (wire signals + internal msgs) ---
# THE AVDTP wire-signal dispatcher. [r1] is the message-type byte:
#   0x01..0x0d → AVDTP wire signal_id (DISCOVER..DELAYREPORT)
#   0x0e+      → internal state-machine events (drove the prior 267 fires
#                with msg=0x17 in the absence of a peer pair attempt)
# To get a wire fire here you MUST drive a fresh pair attempt while gdb
# is attached. After re-pair, expect to see [r1] = 1, 2 (and 3, 4, 6, 7
# during stream setup), then NACK on 0x0c if the peer probes 1.3 features.
break *${BP_aa72c}
commands
silent
printf "BP@dispatcher:0xaa72c: r0=0x%x r1=0x%x  msg=[r1]=0x%02x  LR=0x%x\n", \$r0, \$r1, *(unsigned char*)\$r1, \$lr
printf "  msg-struct@[r1][0..31]:\n"
x/32xb \$r1
continue
end

# --- 0xaa924: sig 0x02 GET_CAPABILITIES handler entry ---
# Fires when peer sends sig 0x02 (mandatory in pair handshake). Capture
# r4/r5/r6 to understand the response builder calling convention. The
# value of [r6+1] BEFORE the handler runs tells us if it's the wire
# sig_id or a state byte (V5 design risk hinges on this).
break *${BP_aa924}
commands
silent
printf "BP@sig02:0xaa924 GET_CAPABILITIES handler: r4=0x%x r5=0x%x r6=0x%x\n", \$r4, \$r5, \$r6
printf "  [r6+0..7]:\n"
x/8xb \$r6
printf "  [r4+0..15]:\n"
x/16xb \$r4
continue
end

# --- 0xab4de: sig 0x0c GET_ALL_CAPABILITIES stub entry ---
# Fires only if peer sends sig 0x0c. Captures the gate state ([r4+8])
# that decides whether the stub goes to error path 0xab51a or attempts
# the (mostly-no-op) main path.
break *${BP_ab4de}
commands
silent
printf "*** BP@sig0c:0xab4de GET_ALL_CAPABILITIES stub fired ***  [r4+8]=0x%02x [r4+9]=0x%02x  LR=0x%x\n", *(unsigned char*)(\$r4+8), *(unsigned char*)(\$r4+9), \$lr
continue
end

# --- 0xaeb9c: response sender called from sig 0x02 handler at 0xaa948 ---
# Critical for V5 design: identifies whether the response wire sig_id is
# carried via r0/r1/r2 args or via a struct field. r0 likely points to
# the response payload buffer; first 16 bytes show the AVDTP response
# header (which must mirror the request's sig_id).
break *${BP_aeb9c}
commands
silent
printf "BP@resp-sender:0xaeb9c: r0=0x%x r1=0x%x r2=0x%x  LR=0x%x\n", \$r0, \$r1, \$r2, \$lr
printf "  resp@[r0][0..15]:\n"
x/16xb \$r0
continue
end

# --- 0xafeec: AvdtpSigParseConfigCmd (named via log-string xref at 0xea67a) ---
# Fires on sig 0x03 SET_CONFIGURATION dispatch. Confirms wire-RX flow
# reached the per-sig parser layer.
break *${BP_afeec}
commands
silent
printf "BP@AvdtpSigParseConfigCmd:0xafeec: r0=0x%x r1=0x%x r2=0x%x  LR=0x%x\n", \$r0, \$r1, \$r2, \$lr
printf "  cmd-struct@[r1][0..15]:\n"
x/16xb \$r1
continue
end

# --- 0xafd5c: AVDTP capability parser (cap MAX_CAPABILITY_SIZE assert) ---
# Fires inside GET_CAPABILITIES / GET_ALL_CAPABILITIES response build.
break *${BP_afd5c}
commands
silent
printf "BP@cap-parser:0xafd5c: r0=0x%x r1=0x%x r2=0x%x  LR=0x%x\n", \$r0, \$r1, \$r2, \$lr
continue
end

# --- 0xb0c30: AVDTP state-machine driver (positive control) ---
# Fires continuously during stream lifecycle (267+ fires per session
# observed). Captures aren't wire-RX but confirm AVDTP layer is alive.
break *${BP_b0c30}
commands
silent
printf "BP@b0c30 state-driver: r0=0x%x r1=0x%x  state=[r1]=0x%02x [r1+1]=0x%02x  LR=0x%x\n", \$r0, \$r1, *(unsigned char*)\$r1, *(unsigned char*)(\$r1+1), \$lr
continue
end

# --- 0xb0b50: AVDTP helper (called from inside b0c30) ---
break *${BP_b0b50}
commands
silent
printf "BP@b0b50 helper: r0=0x%x r1=0x%x r2=0x%x  LR=0x%x\n", \$r0, \$r1, \$r2, \$lr
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

# HOST_GDB was validated up-front (see check after gdbserver discovery).
echo "    ${HOST_GDB} -x ${GDB_CMDS} <path-to-stock-mtkbt>"
echo "    # symbols are optional but help with disassembly; the stock mtkbt"
echo "    # extracted from /work/v3.0.2/system.img.extracted/bin/mtkbt works fine"

echo
echo "Then drive a peer-side AVDTP exchange:"
echo "    1. Pair Y1 with any A2DP Sink (Sonos / Bolt / TV)."
echo "    2. The pairing exchange will issue DISCOVER (sig 0x01) +"
echo "       GET_CAPABILITIES (sig 0x02) — breakpoints will fire."
echo "    3. The critical capture is BP@0x50c46 / BP@0xde2a — those tell"
echo "       us where the dispatcher branches based on the parsed sig_id."
echo "       The 'disassemble \$pc' output at BP@0xde2a is the dispatch site."
echo "    Output is logged to /tmp/mtkbt-gdb-avdtp.log."
echo

# Run gdbserver in foreground; ctrl-C kills it (and mtkbt's ptrace slot is freed
# when gdb on the host detaches). The gdbserver process exits when the gdb
# client disconnects.
adb shell "su -c '/data/local/tmp/gdbserver --attach :${PORT} ${MTKBT_PID}'"
