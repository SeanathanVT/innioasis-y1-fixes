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

# Radare2 xref analysis identified these as functions containing assertion
# strings from avdtp.c / avsigmgr.c — i.e., functions whose source lives in
# mtkbt's AVDTP layer. Put BPs at each entry; at least one must fire during
# AVDTP signaling.
#
# avdtp.c functions (src/.../avdtp/avdtp.c):
BP_af4cc=$(fileoff_to_live 0xaf4cc)   # avdtp.c fn (4 callers; xrefs Stream lookup)
BP_af698=$(fileoff_to_live 0xaf698)   # avdtp.c fn

# avsigmgr.c functions (src/.../avdtp/avsigmgr.c — the signal manager):
BP_aedd8=$(fileoff_to_live 0xaedd8)   # avsigmgr.c large dispatcher (size 6616, 12 out-edges)
BP_afd5c=$(fileoff_to_live 0xafd5c)   # avsigmgr.c capability parser ("n <= MAX_CAPABILITY_SIZE" assert)
BP_b01b4=$(fileoff_to_live 0xb01b4)   # avsigmgr.c stream-id lookup ("strm->locStrmId != 0xFF")
BP_b0270=$(fileoff_to_live 0xb0270)   # avsigmgr.c stream lookup ("Stream->locStrmId != 0xFF")
BP_b0468=$(fileoff_to_live 0xb0468)   # avsigmgr.c fn (called from fcn.000af624)
BP_b0b50=$(fileoff_to_live 0xb0b50)   # major orchestrator — calls b01b4/b0270, called from b18a8
BP_b15a0=$(fileoff_to_live 0xb15a0)   # 1070-byte fn — likely the AVDTP signal RX dispatcher
                                      # (contains the call to b0b50 + multiple sig handler bls)

# Keep the original parser BPs just in case (low cost):
BP_50b08=$(fileoff_to_live 0x50b08)   # parser entry (case dispatch on input fmt-id)
BP_50b96=$(fileoff_to_live 0x50b96)   # parser case-1 sig_id store (= AVDTP sig_id if hit)
BP_50c46=$(fileoff_to_live 0x50c46)   # parser convergence — about to bl validator + return

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

# AVDTP signal-frame parser entry (file 0x50b08). Input arg r0 points at a
# struct: byte 0 = format-id (0/1/2/4/0xff), offset 4 = pointer to inbound
# AV/C frame buffer. LR tells us who called the parser.
break *${BP_50b08}
commands
silent
printf "BP@0x50b08 parser entry: r0=0x%08x [r0+0]=%u (fmt-id) [r0+4]=0x%08x (frame ptr)  LR=0x%08x\n", \$r0, *(unsigned char*)\$r0, *(unsigned int*)(\$r0+4), \$lr
printf "  frame@[r0+4][0..15]: "
x/8xb *(unsigned int*)(\$r0+4)
continue
end

# Parser stores sig_id at output struct +8 (file 0x50b96).
# At this point, register lr (low byte) holds the masked sig_id (& 0x3f).
break *${BP_50b96}
commands
silent
printf "BP@0x50b96 sig_id stored: lr-low=0x%02x (sig_id) → [r1+8] (r1=0x%08x)\n", \$lr & 0xff, \$r1
continue
end

# Parser convergence point (file 0x50c46) — about to bl 0x50a48 (validator)
# then return. r0 / r1 hold the parsed-struct pointer.
break *${BP_50c46}
commands
silent
printf "BP@0x50c46 parser exit: r0=0x%x r1=0x%x  output struct sig_id=0x%02x\n", \$r0, \$r1, *(unsigned char*)(\$r1+8)
continue
end

# --- avdtp.c / avsigmgr.c function-entry BPs (radare2-located) ---
# At least one of these MUST fire during AVDTP signal RX.

break *${BP_af4cc}
commands
silent
printf "BP@avdtp.c:0xaf4cc fcn entry: r0=0x%x r1=0x%x r2=0x%x  LR=0x%x\n", \$r0, \$r1, \$r2, \$lr
continue
end

break *${BP_af698}
commands
silent
printf "BP@avdtp.c:0xaf698 fcn entry: r0=0x%x r1=0x%x r2=0x%x  LR=0x%x\n", \$r0, \$r1, \$r2, \$lr
continue
end

break *${BP_aedd8}
commands
silent
printf "BP@avsigmgr.c:0xaedd8 LARGE fcn entry: r0=0x%x r1=0x%x r2=0x%x r3=0x%x  LR=0x%x\n", \$r0, \$r1, \$r2, \$r3, \$lr
continue
end

break *${BP_afd5c}
commands
silent
printf "BP@avsigmgr.c:0xafd5c (cap parser): r0=0x%x r1=0x%x r2=0x%x  LR=0x%x\n", \$r0, \$r1, \$r2, \$lr
continue
end

break *${BP_b01b4}
commands
silent
printf "BP@avsigmgr.c:0xb01b4 (strm-id lookup): r0=0x%x r1=0x%x  LR=0x%x\n", \$r0, \$r1, \$lr
continue
end

break *${BP_b0270}
commands
silent
printf "BP@avsigmgr.c:0xb0270 (stream lookup): r0=0x%x  LR=0x%x\n", \$r0, \$lr
continue
end

break *${BP_b0468}
commands
silent
printf "BP@avsigmgr.c:0xb0468 fcn entry: r0=0x%x r1=0x%x r2=0x%x r3=0x%x  LR=0x%x\n", \$r0, \$r1, \$r2, \$r3, \$lr
continue
end

break *${BP_b0b50}
commands
silent
printf "BP@avsigmgr.c:0xb0b50 ORCHESTRATOR: r0=0x%x r1=0x%x  LR=0x%x\n", \$r0, \$r1, \$lr
printf "  [r0+0x341]=%u [r1+6]=0x%x [r1+8]=0x%x (sig_id?)\n", *(unsigned char*)(\$r0+0x341), *(unsigned short*)(\$r1+6), *(unsigned char*)(\$r1+8)
continue
end

break *${BP_b15a0}
commands
silent
printf "BP@avsigmgr.c:0xb15a0 LIKELY-DISPATCHER: sp=%p  LR=0x%x\n", \$sp, \$lr
printf "  [sp+0x40]=0x%x (likely cmd struct ptr)\n", *(unsigned int*)(\$sp+0x40)
continue
end

# Original parser BP — kept in case one of the avdtp.c fns calls it
break *${BP_50b08}
commands
silent
printf "BP@parser:0x50b08: r0=0x%x [r0+0]=%u (fmt-id) LR=0x%x\n", \$r0, *(unsigned char*)\$r0, \$lr
continue
end

# Case-1 sig_id store (only fires if AVDTP signaling routes through this parser)
break *${BP_50b96}
commands
silent
printf "*** BP@parser:0x50b96 sig_id stored (case 1 = AVDTP path!): sig_id=0x%02x at [r1+8]\n", \$lr & 0xff
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
