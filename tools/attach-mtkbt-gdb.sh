#!/usr/bin/env bash
# attach-mtkbt-gdb.sh — gdbserver-attach the live mtkbt daemon, set BPs at
# the AVCTP-RX classifier + msg-id-emit dispatcher, emit a gdb command
# file. Pre-reqs: --root flashed; gdbserver under tools/, $GDBSERVER,
# or $ANDROID_NDK_HOME/prebuilt/android-arm/gdbserver/ (ARM 32-bit
# static, API 17). Host: gdb-multiarch / arm-linux-gnu-gdb.
# Run --help for usage.

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

BP_6da7a=$(fileoff_to_live 0x6da7a)   # inner TBH dispatcher (event subtype byte)
BP_6db7c=$(fileoff_to_live 0x6db7c)   # classifier — ldrb [r5,#5]; cmp #1; bhi
BP_6dc36=$(fileoff_to_live 0x6dc36)   # event_code=4 setter (AV/C parse path)
BP_6dc52=$(fileoff_to_live 0x6dc52)   # event_code=8 setter (raw-forward path)
BP_515ca=$(fileoff_to_live 0x515ca)   # dispatcher case 3 (event 4 → msg 506)
BP_51622=$(fileoff_to_live 0x51622)   # dispatcher case 7 (event 8 → msg 519)
BP_fde8=$(fileoff_to_live 0x0fde8)    # blx [r6-4] in fn 0xfb04 — the AV/C handler dispatch
BP_14b48=$(fileoff_to_live 0x14b48)   # fn 0x147dc TBH case 4 (event_code 4 → bl 0x145b0)
BP_14b50=$(fileoff_to_live 0x14b50)   # return from bl 0x145b0 (within fn 0x147dc case 4)
BP_144bc=$(fileoff_to_live 0x144bc)   # downstream handler — fires only if [conn+172]==0 path taken
BP_11374=$(fileoff_to_live 0x11374)   # fn 0x11374 entry — both call this; gate is inside or below
BP_113f2=$(fileoff_to_live 0x113f2)   # bne to 0x11484 (gates on [r8+r6] == 4)
BP_308ea=$(fileoff_to_live 0x308ea)   # cmp r6, #8 — entry to outer-dispatcher invocation block
BP_308f4=$(fileoff_to_live 0x308f4)   # cmp [conn+0x5d5], #1 — the should-forward flag check
BP_3090e=$(fileoff_to_live 0x3090e)   # blx [conn+0x5cc] — actual outer dispatcher invocation

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

# mtkbt is all Thumb-2. force-mode thumb makes gdb plant a 2-byte Thumb BKPT
# at every breakpoint regardless of address parity / symbol info. fallback-mode
# is the looser version (only when gdb can't otherwise decide) — keep both so
# disassembly + BP planting are unambiguous.
set arm fallback-mode thumb
set arm force-mode thumb

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

# blx [r6-4] in fn 0xfb04 — the AV/C handler. r3 holds the callback fn ptr we
# need to identify. Compare r3 between PASSTHROUGH and VENDOR_DEPENDENT frames
# to see whether they dispatch to the same handler (op_code branch inside) or
# to different ones (which would be the cleanest patch site).
break *${BP_fde8}
commands
silent
printf "BP@0x0fde8 (blx [r6-4]): r3=0x%x (callback fn ptr) r0=0x%x r1=0x%x [sp+32]=%u (event_code)\n", \$r3, \$r0, \$r1, *(unsigned char*)(\$sp+32)
continue
end

# fn 0x147dc TBH case 4 entry — fires whenever the [r6-4] callback dispatches
# event_code=4 to the AV/C-event handler chain.
break *${BP_14b48}
commands
silent
printf "BP@0x14b48 (case 4 entry in fn 0x147dc): r4=0x%x r5=0x%x\n", \$r4, \$r5
continue
end

# Right after bl 0x145b0 returns. If both PASSTHROUGH and VENDOR_DEPENDENT
# reach here, the msg 519 divergence is downstream of 0x145b0.
break *${BP_14b50}
commands
silent
printf "BP@0x14b50 (after bl 0x145b0 returns): r0=%u (return val)\n", \$r0
continue
end

# Direct check: does PASSTHROUGH reach fn 0x144bc? My static analysis says no
# (the cbz at 0x14632 takes the early-return branch for both PASSTHROUGH and
# VENDOR_DEPENDENT), but empirics > theory.
break *${BP_144bc}
commands
silent
printf "BP@0x144bc (downstream handler): r0=0x%x r1=0x%x r2=0x%x\n", \$r0, \$r1, \$r2
continue
end

# fn 0x11374 entry — both fn 0x144bc paths bl into 0x11374. Capture r0/r2/r3 to
# see what each frame type passes.
break *${BP_11374}
commands
silent
printf "BP@0x11374 (fn entry): r0=0x%x r1=0x%x r2=0x%x r3=0x%x [sp+12]=0x%x\n", \$r0, \$r1, \$r2, \$r3, *(unsigned int*)(\$sp+12)
continue
end

# 0x113f2 — bne to 0x11484 (the early-return path inside fn 0x11374). PASSTHROUGH
# vs VENDOR_DEPENDENT may diverge here based on [r8+r6] == 4 check at 0x113f0.
break *${BP_113f2}
commands
silent
printf "BP@0x113f2 (gate check inside 0x11374): r0=0x%x r5=0x%x\n", \$r0, \$r5
continue
end

# Outer dispatcher invocation block (0x308ea+). The conn+0x5d5 flag gates
# whether the outer dispatcher fires for event 8. Capture both arms:
break *${BP_308ea}
commands
silent
printf "BP@0x308ea (outer invoke gate): r6=%u (event_code) [conn+0x5d5]=%u\n", \$r6, *(unsigned char*)(\$r4+0x5d5)
continue
end

break *${BP_308f4}
commands
silent
printf "BP@0x308f4 ([conn+0x5d5]==1 check): r0=%u (the loaded byte)\n", \$r0
continue
end

# Actual outer-dispatcher invocation. If this fires, msg 519 will follow.
# This BP being hit for PASSTHROUGH but not VENDOR_DEPENDENT confirms the gate
# is upstream of here (in conn+0x5d5's value or how event_code 8 gets queued).
break *${BP_3090e}
commands
silent
printf "BP@0x3090e (blx [conn+0x5cc]): r1=0x%x (callback fn ptr)\n", \$r1
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
echo "Then drive the peer-CT scenario:"
echo "    1. BT off → on on the Y1, let the CT reconnect"
echo "    2. Look for 'BP@0x6db7c' line — should fire once for VENDOR_DEPENDENT"
echo "       GetCapabilities (14B payload) shortly after AVCTP connect."
echo "    3. Press pause on the CT — should fire BP@0x6db7c again (8B PASSTHROUGH)."
echo "    4. Compare [r5+5] values + payload byte 2 (AV/C op_code) between the two."
echo "    Output is logged to /tmp/mtkbt-gdb.log."
echo

# Run gdbserver in foreground; ctrl-C kills it (and mtkbt's ptrace slot is freed
# when gdb on the host detaches). The gdbserver process exits when the gdb
# client disconnects.
adb shell "su -c '/data/local/tmp/gdbserver --attach :${PORT} ${MTKBT_PID}'"
