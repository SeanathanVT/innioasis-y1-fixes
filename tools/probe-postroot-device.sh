#!/system/bin/sh
# On-device probe — runs as root via su.
# Stock Android 4.2.2 toybox is MISSING: awk, head, tail, pidof.
# This script uses only shell builtins (read, case, while) for limiting/filtering.

hr() { echo; echo "=== $* ==="; }

# Pure-shell head/tail substitutes via `read` builtin.
# Usage: <cmd> | limit_first N   (= head -n N)
#        <cmd> | limit_last  N   (= tail -n N) — buffers in memory; only for short input.
limit_first() {
    n=$1; i=0
    while [ $i -lt $n ] && IFS= read -r L; do
        echo "$L"
        i=$((i+1))
    done
}
limit_last() {
    n=$1
    # ring buffer in shell — slow but works without awk
    # capture all input, then echo last N. uses set -- to track positions.
    set --
    while IFS= read -r L; do
        set -- "$@" "$L"
        if [ $# -gt $n ]; then shift; fi
    done
    for L in "$@"; do echo "$L"; done
}

# pidof replacement using ps + case match (no pidof, no awk on device).
pid_of() {
    target="$1"
    ps 2>/dev/null | while IFS= read -r row; do
        case "$row" in
            *" $target"|*"/$target"|*" $target "*|*"/$target "*)
                # toybox ps columns: USER PID PPID VSIZE RSS WCHAN PC NAME
                # so PID is field 2. extract via shell tokenization (no awk).
                set -- $row
                echo "$2"
                return
                ;;
        esac
    done
}

hr "0. identity + uname"
echo "shell uid: $(id 2>&1)"
echo "uname:     $(uname -a 2>&1)"
echo "ps avail:  $(type ps 2>&1)"

hr "1. mtkbt PID + maps"
PID=$(pid_of mtkbt)
echo "mtkbt pid: ${PID:-<not running per ps>}"
echo "init.svc.mtkbt: $(getprop init.svc.mtkbt)"
if [ -z "$PID" ]; then
    echo "-- ps output (look for mtkbt manually):"
    ps 2>&1 | grep -i mtkbt
fi
if [ -n "$PID" ] && [ -d "/proc/$PID" ]; then
    echo "-- /proc/$PID/maps (first 40 lines):"
    cat /proc/$PID/maps | limit_first 40
    echo
    echo "-- mtkbt segment line(s) (PIE base):"
    grep 'bin/mtkbt' /proc/$PID/maps
    echo
    echo "-- libextavrcp segment lines:"
    grep 'libextavrcp' /proc/$PID/maps
fi

hr "2. MTK xlog ring buffer accessibility"
for path in /proc/mtprintk /proc/mtklog /proc/mtkmt /proc/last_kmsg /proc/aee_dipper /proc/driver/wmt_aee /sys/kernel/debug/wmt_dbg /proc/driver/mtkbt; do
    if [ -e "$path" ]; then
        echo "-- $path: EXISTS"
        ls -l "$path" 2>&1
        if [ -r "$path" ]; then
            echo "   sample (first 1KB if any content):"
            sz=$(wc -c < "$path" 2>/dev/null)
            if [ "${sz:-0}" -gt 0 ] 2>/dev/null; then
                dd if="$path" bs=1024 count=1 2>/dev/null | limit_first 30
            else
                echo "   (size=$sz — empty)"
            fi
        else
            echo "   not readable"
        fi
        echo
    fi
done
echo "-- /proc/driver listing (look for any mtk*/bt* nodes):"
ls -la /proc/driver/ 2>&1 | limit_first 40
echo
echo "-- /sys/kernel/debug listing (mtk/bt subdirs only):"
ls /sys/kernel/debug/ 2>&1 | grep -iE 'mtk|bt|wmt|hci|stp' | limit_first 30

hr "3. canonical btsnoop file paths"
for path in \
    /data/misc/bluedroid/btsnoop_hci.log \
    /data/misc/bluedroid \
    /sdcard/mtklog/btlog \
    /sdcard/mtklog \
    /data/log/btsnoop_hci.log \
    /data/log \
    /data/btmtk \
    /data/misc/bluetooth ; do
    echo "-- $path:"
    if [ -e "$path" ]; then ls -la "$path" 2>&1 | limit_first 10
    else echo "   (does not exist)"; fi
done

hr "4. relevant getprop keys"
getprop | grep -iE 'bt|bluetooth|snoop|mtkbt|avrcp|persist'

hr "5. dmesg — AVRCP/AVCTP/STP/HCI traces (last 80)"
dmesg 2>/dev/null | grep -iE 'avrcp|avctp|stpbt|bluetooth|mtkbt|btmtk|hci' | limit_last 80

hr "6. /dev bt char devices"
ls -la /dev/stpbt /dev/stpwmt /dev/stpfm /dev/stpgps 2>&1

hr "7. mtkbt strings — snoop/persist knob check"
strings /system/bin/mtkbt 2>/dev/null | grep -iE 'snoop|persist\.bt|persist\.mtk|persist\.bluetooth|btsnoop|HciLogger' | limit_first 80

hr "8. libbluetooth*.so — same"
for lib in libbluetoothdrv.so libbluetooth_mtk.so libbluetoothem_mtk.so libbluetooth_relayer.so; do
    echo "-- /system/lib/$lib:"
    strings /system/lib/$lib 2>/dev/null | grep -iE 'snoop|persist\.bt|persist\.mtk|persist\.bluetooth|btsnoop|HciLogger' | limit_first 30
done

hr "9. /proc/<pid>/status fields"
if [ -n "$PID" ]; then
    grep -E '^Name|^State|^Uid|^Gid|^CapInh|^CapPrm|^CapEff|^Threads' /proc/$PID/status
fi

hr "10. gdbserver presence anywhere"
for p in /system/bin/gdbserver /system/xbin/gdbserver /data/local/tmp/gdbserver /data/local/gdbserver; do
    if [ -e "$p" ]; then echo "EXISTS: $p"; ls -la "$p"; fi
done

hr "11. SELinux"
getenforce 2>/dev/null || echo "(getenforce missing — SELinux likely disabled on 4.2.2)"
cat /proc/self/attr/current 2>/dev/null

hr "12. ptrace policy"
cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null && echo "  ^ ptrace_scope (0 = no restriction)"
cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null

hr "13. mtkbt FDs + abstract socket"
if [ -n "$PID" ]; then
    echo "-- open FDs:"
    ls -la /proc/$PID/fd 2>&1 | limit_first 60
fi
echo
echo "-- /proc/net/unix bt/avrcp lines (abstract sockets show '@' prefix in 'Path' col):"
grep -iE 'bt|avrcp|extadp' /proc/net/unix 2>/dev/null | limit_first 30

hr "14. extra: what built-in shell utils ARE available?"
for cmd in awk head tail pidof tr cut sed wc dd grep find ps cat ls strings getprop dmesg getenforce; do
    p=$(which $cmd 2>/dev/null)
    if [ -n "$p" ]; then echo "  $cmd: $p"
    else echo "  $cmd: MISSING"; fi
done

hr "DONE"
