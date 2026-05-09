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

# 2026-05-09 capture invalidated the original 0xaa72c hypothesis: the TBH
# function at 0xaa72c is BlueAngel's INTERNAL task-message dispatcher (267
# fires with msg_type=0x17, none in AVDTP wire range 0x01..0x0d). The real
# AVDTP signal RX dispatcher is fcn.000b0c30 — a 6482-byte function with
# 239 basic blocks that radare2's `aaa` skipped because invalid bytes at
# 0xb0c20-0xb0c2e trap its analyser. Manual disasm at 0xb0c30:
#   0xb0c30: push.w {r4-r8,sb,sl,fp,lr}
#   0xb0c34: mov r8, r0                    ; r0 = stream / channel struct
#   0xb0c40: mov r5, r1                    ; r1 = AVDTP signal frame ptr
#   0xb0c42: ldrh r0, [r1, 2]              ; AVDTP header bytes 2-3
#   0xb0c44: ldrb r3, [r1]                 ; r3 = AVDTP byte 0 (header[0])
#   0xb0c4c: cmp r3, 7
#   0xb0c4e: bhi.w 0xb19c8                 ; oob error
#   0xb0c52: tbh [pc, r3, lsl 1]           ; state-machine dispatch on byte 0
# fcn.000b0c30 contains the bl to AvdtpSigParseConfigCmd (fcn.000afeec) at
# 0xb1012, confirming it's on the SET_CONFIGURATION path. The byte-0 dispatch
# is likely on AVDTP state code (8 states), with sig_id parsed downstream.
#
# Capture goal for V5: confirm fcn.000b0c30 is THE AVDTP RX entry by seeing
# it fire on every inbound signal, and capture sig_id from [r1+1] (low 6
# bits of AVDTP byte 1 = signal_id per V13 §8.5).
BP_b0c30=$(fileoff_to_live 0xb0c30)   # AVDTP signal RX dispatcher entry
BP_afeec=$(fileoff_to_live 0xafeec)   # AvdtpSigParseConfigCmd (sig 0x03 path)
BP_b0b50=$(fileoff_to_live 0xb0b50)   # AVDTP helper called from b18a8 (state machine helper)
BP_b1012=$(fileoff_to_live 0xb1012)   # bl AvdtpSigParseConfigCmd site (sig 0x03 dispatch confirm)

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

# --- fcn.000b0c30 entry — AVDTP-layer state-machine dispatcher ---
# 6482-byte function with TBH on [r1] (8 states, 0..7). Hypothesised as
# the AVDTP signal RX path because it contains the bl to AvdtpSigParseConfigCmd
# at 0xb1012, but first capture (2026-05-09 evening) shows byte0=0x00 +
# byte1=0x37 — the small struct at r1 is NOT the raw AVDTP wire frame
# (frame byte 1 in AVDTP V13 §8.5 has sig_id in low 6 bits, max 0x0d, but
# we saw 0x37). So fcn.000b0c30 sits between L2CAP RX and the per-sig
# parsers and reads from a higher-level request struct, not the wire.
# Dump 32 bytes so we can identify which field carries the wire sig_id.
break *${BP_b0c30}
commands
silent
printf "BP@b0c30: r0=0x%x r1=0x%x  state=[r1]=0x%02x [r1+1]=0x%02x [r1+2..3]=0x%04x  LR=0x%x\n", \$r0, \$r1, *(unsigned char*)\$r1, *(unsigned char*)(\$r1+1), *(unsigned short*)(\$r1+2), \$lr
printf "  struct@[r1][0..31]:\n"
x/32xb \$r1
continue
end

# --- AvdtpSigParseConfigCmd entry (named function — sig 0x03 SET_CONFIGURATION) ---
# Confirms SET_CONFIGURATION RX path. Should fire on any peer pair attempt.
# arg2 (r1) is the parsed signal frame; first byte is signal_id-related.
break *${BP_afeec}
commands
silent
printf "BP@AvdtpSigParseConfigCmd:0xafeec: r0=0x%x r1=0x%x r2=0x%x r3=0x%x  LR=0x%x\n", \$r0, \$r1, \$r2, \$r3, \$lr
printf "  arg-r1@[0..15]:\n"
x/16xb \$r1
continue
end

# --- bl AvdtpSigParseConfigCmd site inside fcn.000b0c30 (file 0xb1012) ---
# Dispatches sig 0x03 from inside b0c30 state-machine. r5 carries the
# preserved arg2 from b0c30 entry (the small struct, not wire frame).
break *${BP_b1012}
commands
silent
printf "BP@b1012 (call AvdtpSigParseConfigCmd from b0c30): r4=0x%x r5=0x%x r6=0x%x\n", \$r4, \$r5, \$r6
printf "  r5-struct@[0..15]:\n"
x/16xb \$r5
continue
end

# --- fcn.000b0b50 (AVDTP helper called from b18a8 in fcn.000b0c30) ---
# 214-byte fn. r0/r1 args show what's being dispatched. Useful for
# tracing the state-machine flow inside b0c30.
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
