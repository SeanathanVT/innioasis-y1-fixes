# su

Minimal setuid-root escalator for the Innioasis Y1 research device. Installed at `/system/xbin/su` (mode 06755, root:root) by the top-level bash's `--root` flag.

## Why this exists

The H1/H2/H3 byte patches in `/sbin/adbd` (see `../patches/patch_adbd.py`) all caused "device offline" on hardware in every revision tried. This `su` sidesteps the issue: stock `/sbin/adbd` stays untouched and runs at uid 2000 (shell) as normal, ADB protocol comes up cleanly, and root is obtained post-flash via `adb shell /system/xbin/su`.

## Files

- **`su.c`** — direct ARM-EABI syscall implementation, no libc. `setgid(0)` → `setuid(0)` → `execve("/system/bin/sh", …)`. Three invocation forms: bare `su` (interactive root shell), `su -c "<cmd>"` (one-off), `su <prog> [args…]` (exec-passthrough).
- **`start.S`** — ARM Thumb-2 entry stub. Extracts argc/argv/envp from the ELF process-start stack layout, calls `main`, exits via `__NR_exit`.
- **`Makefile`** — cross-compile via `arm-linux-gnu-gcc`. `-nostdlib -ffreestanding -static -Os -mthumb -mfloat-abi=soft`; ARMv7-A target.

## Build

Toolchain install on Rocky/Alma/RHEL/Fedora:

```bash
sudo dnf install -y epel-release
sudo dnf install -y gcc-arm-linux-gnu binutils-arm-linux-gnu make
```

Debian/Ubuntu equivalent: `sudo apt install gcc-arm-linux-gnueabi binutils-arm-linux-gnueabi make`.

Then:

```bash
make           # → build/su
make check     # asserts ARM ELF + statically linked + no NEEDED entries
make clean
```

Output is ~900 bytes, statically linked, stripped, no dynamic dependencies. Idempotent — re-running `make` is a no-op if sources are unchanged.

## Deploy

The top-level bash does this for you:

```bash
./apply.bash --artifacts-dir <path> --root
```

Equivalent manual install (against the mounted system.img):

```bash
sudo install -m 06755 -o root -g root build/su /mnt/y1-devel/xbin/su
```

## Verify post-flash

```bash
adb shell /system/xbin/su -c id          # → uid=0(root) gid=0(root)
adb shell /system/xbin/su -c "logcat -b all -d | head"
adb shell /system/xbin/su                # interactive root shell
```

## Trade-offs

- **No supply chain beyond GCC + this source.** No SuperSU/Magisk/phh-style binary imported; no manager APK; no whitelist.
- **Any process that can exec `/system/xbin/su` becomes root.** Acceptable for a single-user research device. Not appropriate for a consumer ROM.
- The binary is intentionally tiny and direct so every byte is auditable.

## See also

- [`../../README.md`](../../README.md) — project overview
- [`../../docs/PATCHES.md`](../../docs/PATCHES.md) — how this fits into the broader patch set
- [`../../INVESTIGATION.md`](../../INVESTIGATION.md) — the AVRCP-debugging context that motivated needing root
- [`../../CHANGELOG.md`](../../CHANGELOG.md) — top-level changelog
