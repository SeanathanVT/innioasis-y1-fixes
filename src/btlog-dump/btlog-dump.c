/*
 * btlog-dump.c — connect to mtkbt's @btlog abstract UNIX socket and dump.
 *
 * mtkbt creates a SOCK_STREAM listening socket at the abstract UNIX address
 * "btlog" (sun_path[0] = 0, then "btlog") via socket_local_server() at
 * mtkbt vaddr 0x6b4d4. Anyone with sufficient permission can connect()
 * and read whatever stream mtkbt pushes — most likely __xlog_buf_printf
 * output that is otherwise invisible to logcat.
 *
 * Usage on device (as root, since mtkbt's listener perms drop FDs to bluetooth uid):
 *   /data/local/tmp/btlog-dump > /sdcard/btlog.txt
 *
 * Build (host): make -C src/btlog-dump
 * Push (host):  adb push src/btlog-dump/build/btlog-dump /data/local/tmp/
 *
 * No-libc, direct ARM-EABI syscalls. Same toolchain + style as src/su/.
 */

#include <stddef.h>

/* ARM EABI syscall numbers (Linux kernel >= 2.6.36). */
#define __NR_exit       1
#define __NR_read       3
#define __NR_write      4
#define __NR_close      6
#define __NR_socket     281
#define __NR_connect    283

#define AF_UNIX         1
#define SOCK_STREAM     1

#define STDOUT          1
#define STDERR          2

/* sockaddr_un on Linux: u16 family + 108 bytes path. */
struct sockaddr_un {
    unsigned short sun_family;
    char           sun_path[108];
};

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

static int  sys_socket(int dom, int type, int proto)              { return (int)syscall3(__NR_socket, dom, type, proto); }
static int  sys_connect(int fd, const void *addr, unsigned len)   { return (int)syscall3(__NR_connect, fd, (long)addr, len); }
static long sys_read(int fd, void *buf, unsigned long n)          { return syscall3(__NR_read, fd, (long)buf, n); }
static long sys_write(int fd, const void *buf, unsigned long n)   { return syscall3(__NR_write, fd, (long)buf, n); }
static void sys_exit(int code)                                    { syscall1(__NR_exit, code); for(;;) {} }

static unsigned my_strlen(const char *s) {
    unsigned n = 0; while (s[n]) n++; return n;
}

static void put_err(const char *s) {
    sys_write(STDERR, s, my_strlen(s));
}

/* Format a long as 8-char hex (no division → no libgcc helpers needed). */
static void put_hex_err(unsigned long v) {
    static const char H[] = "0123456789abcdef";
    char b[10]; b[0] = '0'; b[1] = 'x';
    for (int i = 0; i < 8; i++) {
        b[2 + i] = H[(v >> ((7 - i) * 4)) & 0xf];
    }
    sys_write(STDERR, b, 10);
}

int main(int argc, char **argv, char **envp) {
    (void)argc; (void)argv; (void)envp;

    int fd = sys_socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) { put_err("socket() failed errno="); put_hex_err(-fd); put_err("\n"); sys_exit(1); }

    struct sockaddr_un sa;
    /* zero-init */
    char *p = (char *)&sa;
    for (unsigned i = 0; i < sizeof(sa); i++) p[i] = 0;

    sa.sun_family = AF_UNIX;
    /* abstract namespace: leading NUL then name */
    sa.sun_path[0] = 0;
    sa.sun_path[1] = 'b';
    sa.sun_path[2] = 't';
    sa.sun_path[3] = 'l';
    sa.sun_path[4] = 'o';
    sa.sun_path[5] = 'g';

    /* addrlen = sizeof(sun_family) + 1 (leading NUL) + name_len */
    unsigned addrlen = 2 + 1 + 5;
    int rc = sys_connect(fd, &sa, addrlen);
    if (rc < 0) {
        put_err("connect(@btlog) failed errno=");
        put_hex_err(-rc);
        put_err("\n");
        sys_exit(2);
    }

    put_err("connected to @btlog, dumping...\n");

    char buf[4096];
    for (;;) {
        long n = sys_read(fd, buf, sizeof(buf));
        if (n == 0) {
            put_err("EOF on @btlog\n");
            break;
        }
        if (n < 0) {
            put_err("read failed errno=");
            put_hex_err(-n);
            put_err("\n");
            break;
        }
        long off = 0;
        while (off < n) {
            long w = sys_write(STDOUT, buf + off, n - off);
            if (w <= 0) { put_err("write failed\n"); sys_exit(3); }
            off += w;
        }
    }
    return 0;
}
