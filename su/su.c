/*
 * su.c — minimal setuid-root escalator for the Innioasis Y1 research device.
 *
 * Target: MT6572, Android 4.2.2 (JDQ39), kernel ~3.4, ARMv7 Thumb-2 EABI.
 * Deploy: /system/xbin/su, owner root:root, mode 06755 (setuid + rwxr-xr-x).
 *
 * No-libc build. The ARM cross-gcc packaged in EPEL ships only libgcc.a (no
 * glibc/musl target libs), so this file uses direct ARM-EABI syscalls instead.
 * Side effect: the output binary has no third-party libc supply chain at all
 * — every byte traces to GCC + this source + start.S. Output is ~1-2 KB.
 *
 * Design intent — research tool, not a consumer rooting solution:
 *   - No permission-prompting UI, no manager APK, no whitelist.
 *   - Any process that can exec /system/xbin/su becomes root. On this device
 *     the only thing that runs under uid 2000 (shell) is adbd-spawned shells,
 *     and the device is single-user.
 *   - Stock /sbin/adbd stays untouched, sidestepping the H1/H2/H3 failure
 *     mode where patched adbd put the device in "device offline".
 *
 * Usage (post-flash, from the host):
 *   adb shell /system/xbin/su                          # interactive root shell
 *   adb shell /system/xbin/su -c "logcat -b all"       # one-off command as root
 *   adb shell /system/xbin/su /system/bin/some-cmd     # exec specific program
 */

#include <stddef.h>

/* ARM-EABI syscall numbers (Linux). Stable since kernel 2.4-era. */
#define __NR_exit       1
#define __NR_write      4
#define __NR_execve     11
#define __NR_setuid32   213
#define __NR_setgid32   214

/* ARM-EABI syscall convention: nr in r7, args in r0-r5, result in r0. */
static inline long syscall1(int nr, long a0) {
    register long r0 asm("r0") = a0;
    register int  r7 asm("r7") = nr;
    asm volatile ("svc 0" : "+r"(r0) : "r"(r7) : "memory");
    return r0;
}

static inline long syscall3(int nr, long a0, long a1, long a2) {
    register long r0 asm("r0") = a0;
    register long r1 asm("r1") = a1;
    register long r2 asm("r2") = a2;
    register int  r7 asm("r7") = nr;
    asm volatile ("svc 0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return r0;
}

static int sys_setuid(unsigned uid)  { return (int)syscall1(__NR_setuid32, uid); }
static int sys_setgid(unsigned gid)  { return (int)syscall1(__NR_setgid32, gid); }
static int sys_execve(const char *path, char *const argv[], char *const envp[]) {
    return (int)syscall3(__NR_execve, (long)path, (long)argv, (long)envp);
}
static long sys_write(int fd, const void *buf, unsigned long n) {
    return syscall3(__NR_write, fd, (long)buf, n);
}

static unsigned long my_strlen(const char *s) {
    unsigned long n = 0;
    while (s[n]) n++;
    return n;
}
static int my_strcmp(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}
static void emit_err(const char *s) { sys_write(2, s, my_strlen(s)); }

int main(int argc, char **argv, char **envp) {
    if (sys_setgid(0) != 0) { emit_err("su: setgid(0) failed\n"); return 1; }
    if (sys_setuid(0) != 0) { emit_err("su: setuid(0) failed\n"); return 1; }

    static const char SHELL[] = "/system/bin/sh";

    if (argc < 2) {
        char *sh_argv[] = { (char *)"sh", NULL };
        sys_execve(SHELL, sh_argv, envp);
        emit_err("su: execve(sh) failed\n");
        return 1;
    }

    if (my_strcmp(argv[1], "-c") == 0) {
        if (argc < 3) { emit_err("su: -c requires a command argument\n"); return 1; }
        char *sh_argv[] = { (char *)"sh", (char *)"-c", argv[2], NULL };
        sys_execve(SHELL, sh_argv, envp);
        emit_err("su: execve(sh -c) failed\n");
        return 1;
    }

    sys_execve(argv[1], &argv[1], envp);
    emit_err("su: execve failed\n");
    return 1;
}
