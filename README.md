# Koensayr

> Innioasis Y1 firmware patcher & research toolkit (MT6572 / Android 4.2.2)

(The project name is a Star Wars deep cut: Koensayr Manufacturing made the Y-Wing starfighter; Y-Wing → Y1.)

## Overview

- **Music-player UX** — Artist→Album navigation on the system music APK.
- **Bluetooth pairing** — audio.conf / auto_pairing.conf / blacklist.conf / build.prop edits for car and headset pairing.
- **System config** — enable ADB debugging, remove preinstalled bloatware.
- **Root** — install `/system/xbin/su` (setuid, mode 06755) for `adb shell /system/xbin/su` escalation. Stock `/sbin/adbd` stays untouched.
- **AVRCP 1.3 metadata + control over Bluetooth** — peer Controller sees full track metadata, ms-precision playhead, track / battery notifications, and bidirectional Repeat / Shuffle. Spec-compliant 1.3 TG (§6.7.1 / §6.6.1 / §5.3.4).
- **Investigation tooling** — diagnostic scripts (`@btlog` tap, dual-capture, post-root probe, gdbserver attach). Not invoked by the patch flow — see [Diagnostics](#diagnostics).

Compatibility is defined by [`KNOWN_FIRMWARES`](#stock-firmware-manifest) in `apply.bash`; add a row to enrol a new build.

## Layout

The bash entry-point at the root dispatches into source trees under `src/`:

- `apply.bash` — single entry point; flag-driven dispatch into the trees below
- [`src/patches/`](src/patches/) — byte/smali patchers (`patch_*.py`); see [`src/patches/README.md`](src/patches/README.md) for the per-patcher table and [`docs/PATCHES.md`](docs/PATCHES.md) for byte-level detail
- [`src/su/`](src/su/) — minimal setuid-root `su` for `--root` (~900-byte direct-syscall ARM-EABI ELF, no libc). Build via `cd src/su && make`
- [`src/Y1Bridge/`](src/Y1Bridge/) — Android service app source for `Y1Bridge.apk` (consumed by `--avrcp`; hosts the Binder declaration MtkBt resolves to). Build via `cd src/Y1Bridge && ./gradlew --stop && ./gradlew assembleDebug`
- [`src/btlog-dump/`](src/btlog-dump/) — `@btlog` abstract-socket reader (diagnostic; same toolchain as `src/su/`). Build via `cd src/btlog-dump && make`
- `tools/` — setup, diagnostic, and release helpers
- `staging/` — default `--artifacts-dir`; drop `rom.zip` here

## Quick start

One-time setup (clones tooling, builds the prebuilt artifacts `--all` needs):

```bash
./tools/setup.sh                                            # MTKClient + Python venvs
( cd src/su && make )                                        # setuid-su for --root
./tools/install-android-sdk.sh && source tools/android-sdk-env.sh
( cd src/Y1Bridge && ./gradlew --stop && ./gradlew assembleDebug )   # Y1Bridge.apk for --avrcp
```

Then stage `rom.zip` (the official OTA — MD5-validated against [`KNOWN_FIRMWARES`](#stock-firmware-manifest)) and run:

```bash
cp /path/to/rom.zip staging/
./apply.bash --all
```

`--all` = `--adb --avrcp --bluetooth --music-apk --remove-apps --root`.

The bash extracts `system.img` from `rom.zip`, loop-mounts it, applies the patches in-place, unmounts, and flashes via MTKClient. Subdirectory build outputs and `tools/` contents are picked up automatically.

Anything under `staging/` other than its tracked README is `.gitignore`d. **`git clean -dfx` will nuke staged firmware** along with build artifacts — keep a backup of `rom.zip`, or pass `--artifacts-dir <path>` to stage elsewhere.

Override bundled tooling with `--mtkclient-dir <path>` / `--python-venv <path>` (or `MTKCLIENT_DIR` env).

### Flags

| Flag | Effect |
|---|---|
| `--adb` | Append `persist.service.adb.enable=1` + `persist.service.debuggable=1` to `build.prop`. |
| `--avrcp` | AVRCP 1.3 metadata pipeline: patches `mtkbt`, `libextavrcp.so`, `libextavrcp_jni.so`, `MtkBt.odex`, the music app, plus `Y1Bridge.apk` install. Pre-requires `gradlew assembleDebug` in `src/Y1Bridge/`. Patch ID legend in [`docs/PATCHES.md`](docs/PATCHES.md); architecture in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). |
| `--bluetooth` | Pairing-essential `audio.conf` / `auto_pairing.conf` / `blacklist.conf` / `build.prop` edits. Required for car pairing. |
| `--music-apk` | Patch Y1 music player APK (Artist→Album navigation). |
| `--remove-apps` | Remove bloatware (`ApplicationGuide`, `BasicDreams`, …). |
| `--root` | Install `src/su/build/su` at `/system/xbin/su` (mode 06755). Pre-requires `make` in `src/su/`. |
| `--all` | All of the above. Pre-requires the `src/su/` + `src/Y1Bridge/` builds. |

Run `./apply.bash --help` for full flag detail. Patchers can also be run standalone — see [`src/patches/README.md`](src/patches/README.md).

## Diagnostics

Post-root tools for investigating AVRCP behaviour on hardware. None are invoked by the patch flow. Pre-req: `--root` flashed.

- **`@btlog` tap** — `src/btlog-dump/` (no-libc ARM ELF) + `tools/dual-capture.sh` (push + run + capture btlog & logcat) + `tools/btlog-parse.py` (decode framing).
- **Post-root probe** — `tools/probe-postroot.sh` + `tools/probe-postroot-device.sh`. Enumerates PIE base, debug nodes, btsnoop paths, `getprop` keys, ptrace policy, abstract sockets.
- **gdbserver attach to mtkbt** — `tools/install-gdbserver.sh` + `tools/attach-mtkbt-gdb.sh`. Pulls a pinned static ARM gdbserver, attaches to the live PID, generates a breakpoint command file at the AVCTP-RX classifier + dispatcher arms.

Background on the failed alternatives these tools replace: [`docs/INVESTIGATION.md`](docs/INVESTIGATION.md).

## Status

`--all` produces a working device: Bluetooth pairing, A2DP audio, AVRCP 1.3 metadata + control, `--root`, `--music-apk` / `--remove-apps` / `--adb`. Every Mandatory and Optional ICS Table 7 (Target Features) row closes. Per-row scorecard: [`docs/BT-COMPLIANCE.md`](docs/BT-COMPLIANCE.md). Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Stock firmware manifest

Known stock firmwares recognised by `KNOWN_FIRMWARES` in the bash. Add a row (same five-field schema) to enrol a new build.

| Version | rom.zip (input) | system.img (raw, extracted) | boot.img (in zip; not consumed since v1.7.0) | Music APK basename in `app/` |
|---|---|---|---|---|
| **3.0.2** | `82657db82578a38c6f1877e02407127a` | `473991dadeb1a8c4d25902dee9ee362b` | `1f7920228a20c01ad274c61c94a8cf36` | `com.innioasis.y1_3.0.2.apk` |
| **3.0.7** | `02ae3ae89e20bde0a20e940f73e1ed1b` | `663baf9f7f2a08caa82e3fba7a9baa28` | `83b946d1799b4f0281ba8e808ed7911b` | `com.innioasis.y1_3.0.7.apk` |

The MediaTek BT stack (`bin/mtkbt`, `lib/libextavrcp*.so`, `lib/libaudio.a2dp.default.so`, `app/MtkBt.odex`) is byte-identical between 3.0.2 and 3.0.7 — every native patch in `--avrcp` / `--bluetooth` applies unchanged. Only the music APK differs (resource-ID shifts + a few additions in `Y1Repository`), and `patch_y1_apk.py`'s smali anchors handle both builds.

Stock sizes: 3.0.2 `rom.zip` 259,502,414 bytes (raw `system.img` inside); 3.0.7 `rom.zip` 189,791,144 bytes (sparse `system.img` inside, auto-de-sparsed via `simg2img`). Both `system.img`s expand to 681,574,400 bytes raw ext4. `boot.img` 4,706,304 bytes on both.

## Requirements

- **Linux host**, Bash 4+, `sudo`. The patcher uses `mount -o loop` and GNU `sed -i` syntax — both Linux-only. macOS users would need a Linux VM (Lima, OrbStack, UTM) or a remote Linux shell.
- `git`, `unzip`, `md5sum`.
- Python 3.8+ with `venv` module. Patcher byte-level scripts are stdlib-only; `patch_y1_apk.py` needs `androguard`, which `tools/setup.sh` installs into `tools/python-venv/`. Java 11+ also required for `--music-apk` (apktool's smali assembler; apktool itself is downloaded by `patch_y1_apk.py` on first invocation).
- `tools/setup.sh` clones MTKClient (currently pinned to 2.1.4.1) into `tools/mtkclient/` and creates `tools/mtkclient/venv/` with its requirements. Override with `--mtkclient-dir <path>` or `MTKCLIENT_DIR` if you have it elsewhere.
- `simg2img` — only if the matched `KNOWN_FIRMWARES` build bundles a sparse `system.img` (v3.0.2 is raw; v3.0.7 is sparse). Install: `dnf install android-tools` (Fedora / RHEL via EPEL), `apt install android-sdk-libsparse-utils` (Debian / Ubuntu), `pacman -S android-tools` (Arch).
- For `--root` only: prebuilt `src/su/build/su` (`cd src/su && make`). Toolchain: `dnf install -y epel-release && dnf install -y gcc-arm-linux-gnu binutils-arm-linux-gnu make` (Rocky/Alma/RHEL/Fedora) or `gcc-arm-linux-gnueabi` on Debian/Ubuntu.
- For `--avrcp` only: Android SDK + JDK 17+. `tools/install-android-sdk.sh` auto-installs into `tools/android-sdk/` (~1.5 GB, idempotent). Manual instructions: [`docs/ANDROID-SDK.md`](docs/ANDROID-SDK.md).

## Documentation

- [CHANGELOG.md](CHANGELOG.md) — version history (Keep a Changelog format)
- [docs/ANDROID-SDK.md](docs/ANDROID-SDK.md) — Android SDK install instructions (only needed for `--avrcp` / Y1Bridge build)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — AVRCP metadata proxy architecture: data-path diagram, trampoline chain, response-builder calling conventions, ELF segment-extension technique, code-cave inventory. Read this first if working on the metadata pipeline.
- [docs/BT-COMPLIANCE.md](docs/BT-COMPLIANCE.md) — current ICS Table 7 coverage scorecard (every Mandatory + every Optional row)
- [docs/INVESTIGATION.md](docs/INVESTIGATION.md) — chronological AVRCP investigation history, refuted hypotheses, trace log
- [docs/PATCHES.md](docs/PATCHES.md) — per-patch byte-level reference (offsets, before/after bytes, rationale)

## Deployment notes

The patched music-player APK must land in `/system/app/`, not via `adb install` / PackageManager — its stale META-INF only satisfies the parseable-signature requirement when filesystem-deployed at boot. `apply.bash --music-apk` handles this. Manual ADB push:

```bash
adb root && adb remount
adb push com.innioasis.y1_<version>-patched.apk /system/app/com.innioasis.y1/com.innioasis.y1.apk
adb shell chmod 644 /system/app/com.innioasis.y1/com.innioasis.y1.apk
adb reboot
```

## Verified against

Innioasis Y1 — MTK MT6572 ARM, Android 4.2.2. Hardware-verified against the v3.0.2 and v3.0.7 firmwares in [`KNOWN_FIRMWARES`](#stock-firmware-manifest); other builds need a manifest row added and may need patch-site offsets re-located if their stock MD5s diverge.

## Author

Sean Halpin ([github.com/SeanathanVT](https://github.com/SeanathanVT))

## License

GNU General Public License v3.0 (GPLv3) — see [LICENSE](LICENSE).
