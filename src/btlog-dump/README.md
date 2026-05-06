# btlog-dump

Minimal `@btlog` abstract-socket reader for diagnostic capture from `mtkbt`. Pushed to `/data/local/tmp/btlog-dump` and run as root via `su` (typically through [`../../tools/dual-capture.sh`](../../tools/dual-capture.sh)). Output is the structured binary stream that `mtkbt` pushes — decode with [`../../tools/btlog-parse.py`](../../tools/btlog-parse.py).

## Why this exists

`mtkbt` runs a `SOCK_STREAM` listener at the abstract UNIX address `@btlog` (created by `socket_local_server("btlog", ABSTRACT, SOCK_STREAM)` at `mtkbt` vaddr `0x6b4d4`). Anything connecting to it gets a stream of `mtkbt`'s `__xlog_buf_printf` output **plus** decoded HCI command/event traffic — both of which are otherwise invisible to `logcat`. This is the on-device equivalent of `btsnoop_hci.log` and `[AVRCP]/[AVCTP]` xlog output combined into one feed, requiring no `persist.bt.virtualsniff` (which breaks BT init), no kernel-side btsnoop, and no binary patching.

The socket exists in stock firmware; root is only needed because the mtkbt process runs as uid `bluetooth` and the socket inherits its owner. With `/system/xbin/su` (v1.8.0+), connecting is straightforward.

## Files

- **`btlog-dump.c`** — direct ARM-EABI syscall implementation, no libc. `socket(AF_UNIX, SOCK_STREAM)` → `connect()` to abstract `"btlog"` (sun_path[0]=NUL, then "btlog") → loop `read()` to stdout. Zero command-line args; runs until EOF or interrupt.
- **`Makefile`** — cross-compile via `arm-linux-gnu-gcc`. Same `-nostdlib -ffreestanding -static -Os -mthumb -mfloat-abi=soft` flags as `src/su/`. Reuses `../su/start.S` as the entry stub.

## Build

Toolchain install — same as `src/su/`. On Rocky/Alma/RHEL/Fedora:

```bash
sudo dnf install -y epel-release
sudo dnf install -y gcc-arm-linux-gnu binutils-arm-linux-gnu make
```

Debian/Ubuntu equivalent: `sudo apt install gcc-arm-linux-gnueabi binutils-arm-linux-gnueabi make`.

Then:

```bash
make           # → build/btlog-dump (~1 KB static ARM ELF)
make check     # asserts ARM ELF + statically linked + no NEEDED entries
make clean
```

## Use

The expected entry point is `tools/dual-capture.sh`, which pushes the binary, runs it under `su` alongside `logcat -v threadtime`, and writes both streams to a timestamped output dir. Manual:

```bash
adb push build/btlog-dump /data/local/tmp/btlog-dump
adb shell chmod 755 /data/local/tmp/btlog-dump
adb shell 'su -c /data/local/tmp/btlog-dump' > btlog.bin
# Ctrl-C when done
tools/btlog-parse.py btlog.bin --tag-include AVRCP --tag-include AVCTP
```

## Stream format

Decoded by `tools/btlog-parse.py`. Roughly:

| Bytes | Field |
|---|---|
| 1 | Start marker `0x55` |
| 1 | `0x00` pad |
| 1 | Frame body length |
| 2 | Sequence ID (2 ASCII chars; alphabetical, increments per frame) |
| 1 | Severity / category (`0x12` for xlog text, `0xb4` for HCI snoop) |
| 1 | `0x00` pad |
| body[0..1]   | Often constant `00 e5` |
| body[2..6]   | Timestamp (`u32` LE; monotonic per process lifetime, **separate domains per severity**) |
| body[6..10]  | Zero/flag bytes |
| body[10..12] | `u16` LE — typically the format-string base length |
| body[12..]   | Variable-length sub-header (often NUL padding for arg alignment), then the format string + substituted args, NUL-terminated |

Severities seen: `0x12` (xlog text — `[AVRCP]`, `[AVCTP]`, `[L2CAP]`, `[ME]`, `SdpUuidCmp:`, `ConnManager:`, etc.) and `0x07` / `0x08` / `0xb4` (HCI snoop / module-specific).

## Trade-offs

- **No supply chain beyond GCC + this source.** Mirrors `src/su/`'s policy.
- **`@btlog` is undocumented** — the framing was reverse-engineered by inspection (Trace #9 in `../../docs/INVESTIGATION.md` / the live brief). Future MTK firmware revisions could change it; if `tools/btlog-parse.py` produces empty output after a firmware bump, re-derive from the binary stream.
- **Sustained captures fill `/sdcard` fast.** ~80% of typical capture volume is per-byte HCI logging (`[BT]GetByte:`/`[BT]PutByte:`); filter post-hoc with `btlog-parse.py --tag-exclude '[BT] '` or take short captures around the specific scenario you're investigating.

## See also

- [`../../README.md`](../../README.md) — project overview
- [`../../tools/dual-capture.sh`](../../tools/dual-capture.sh) — primary capture wrapper (btlog + logcat in one shot)
- [`../../tools/btlog-parse.py`](../../tools/btlog-parse.py) — frame decoder
- [`../../tools/probe-postroot.sh`](../../tools/probe-postroot.sh) — post-root sanity probe (PIE base, `/proc/net/unix`, ptrace policy, etc.)
- [`../su/`](../su/) — sister no-libc ARM ELF; same toolchain + style
- [`../../docs/INVESTIGATION.md`](../../docs/INVESTIGATION.md) — AVRCP investigation context
- [`../../CHANGELOG.md`](../../CHANGELOG.md) — top-level changelog
