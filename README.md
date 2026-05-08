# Koensayr

> Innioasis Y1 firmware patcher & research toolkit (MT6572 / Android 4.2.2)

A patching toolkit for the Innioasis Y1 media player that improves the music-player UI, provides a setuid-root escalator for on-device debugging, ships diagnostic tooling for the AVRCP investigation, and configures pairing-essential Bluetooth bits. Compatibility is defined by the [`KNOWN_FIRMWARES`](#stock-firmware-manifest) manifest in `apply.bash`; add a row to support a new build.

(The project name is a Star Wars deep cut: Koensayr Manufacturing made the Y-Wing starfighter; Y-Wing → Y1.)

## Overview

- **Music-player UX** — Artist→Album navigation patch on the system music APK (cover art on artist tap instead of a flat song list).
- **Bluetooth pairing** — `audio.conf` / `auto_pairing.conf` / `blacklist.conf` / `build.prop` edits required for car and headset pairing.
- **System config** — enable ADB debugging, remove preinstalled bloatware.
- **Root** — install a minimal `/system/xbin/su` (setuid-root, 06755) for `adb shell /system/xbin/su`-style escalation. Stock `/sbin/adbd` is untouched.
- **AVRCP 1.3 metadata over Bluetooth** — Title/Artist/Album, current play status (with live position), play-state edges and track-change edges delivered to peer Bluetooth Controllers (car head units, TVs, smart speakers). Implemented as a chain of binary trampolines in `libextavrcp_jni.so` that intercept inbound AVRCP commands and call the existing C response-builder primitives directly, bypassing the OEM's no-op Java AVRCP TG. Behind the `--avrcp` flag (excluded from `--all` because it requires a Y1MediaBridge gradle build first). See [Status](#status).
- **AVRCP investigation tooling** — diagnostic scripts (`@btlog` tap, dual-capture with `getevent` + `dumpsys input`, post-root probe, gdbserver attach to mtkbt) used to drive the metadata pipeline above. None are invoked by the patch flow — see [Diagnostics](#diagnostics).

## Layout

The bash entry-point at the root dispatches into source trees under `src/`:

- `apply.bash` — single entry point; flag-driven dispatch into the trees below
- [`src/patches/`](src/patches/) — byte/smali patchers (`patch_*.py`); see [`src/patches/README.md`](src/patches/README.md) for the per-patcher table and [`docs/PATCHES.md`](docs/PATCHES.md) for byte-level detail
- [`src/su/`](src/su/) — minimal setuid-root `su` for `--root` (~900-byte direct-syscall ARM-EABI ELF, no libc). Build via `cd src/su && make`
- [`src/Y1MediaBridge/`](src/Y1MediaBridge/) — Android service app source for `Y1MediaBridge.apk` (consumed by `--avrcp`). Build via `cd src/Y1MediaBridge && ./gradlew --stop && ./gradlew assembleDebug`
- [`src/btlog-dump/`](src/btlog-dump/) — `@btlog` abstract-socket reader (diagnostic; same toolchain as `src/su/`). Build via `cd src/btlog-dump && make`
- `tools/` — setup, diagnostic, and release helpers
- `staging/` — default `--artifacts-dir`; drop `rom.zip` here

## Quick start

Stage `rom.zip` (the official OTA — MD5-validated against [`KNOWN_FIRMWARES`](#stock-firmware-manifest)) inside the repo's `staging/` directory:

```bash
./tools/setup.sh                    # one-time: clone MTKClient + Python venvs
( cd src/su && make )               # one-time: build the setuid-su binary for --root

cp /path/to/rom.zip staging/

./apply.bash --all
```

`--all` = `--adb --bluetooth --music-apk --remove-apps --root`. `--avrcp` is intentionally excluded (see [Status](#status)).

The bash extracts `system.img` from `rom.zip`, loop-mounts it, applies the selected patches in-place, unmounts, and flashes via MTKClient. Subdirectory build outputs and `tools/` contents are picked up automatically.

Anything under `staging/` other than its tracked README is `.gitignore`d. **`git clean -dfx` will nuke staged firmware** along with build artifacts — keep a backup of `rom.zip` if you'd rather not re-download. Pass `--artifacts-dir <path>` to point at a different staging location instead (e.g., on a separate drive, shared between checkouts, or one you'd rather have outside the repo for safety).

Opting in to `--avrcp` additionally needs the Android SDK + `Y1MediaBridge.apk` build:

```bash
./tools/install-android-sdk.sh && source tools/android-sdk-env.sh
( cd src/Y1MediaBridge && ./gradlew --stop && ./gradlew assembleDebug )
```

Override the bundled tooling with `--mtkclient-dir <path>` / `--python-venv <path>` (or `MTKCLIENT_DIR` env) if you have those installed elsewhere.

### Flags

| Flag | Effect |
|---|---|
| `--adb` | Append `persist.service.adb.enable=1` + `persist.service.debuggable=1` to `build.prop`. |
| `--avrcp` | AVRCP 1.3 metadata pipeline: SDP shape patches in `mtkbt` (V1/V2/S1/P1), full trampoline chain in `libextavrcp_jni.so` (R1/T1/T2/extended_T2/T4/T5/T_charset/T_battery/T_continuation/T6/T8/T9 + U1 kernel-auto-repeat NOP), Java-side patches in `MtkBt.odex` (F1/F2 + cardinality NOPs), discrete PASSTHROUGH PLAY/PAUSE/STOP routing + media-key propagation in the music app (Patches E + H), plus `Y1MediaBridge.apk` install. Excluded from `--all` because it requires `assembleDebug` first. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). |
| `--bluetooth` | Pairing-essential `audio.conf` / `auto_pairing.conf` / `blacklist.conf` / `build.prop` edits. Required for car pairing. |
| `--music-apk` | Patch Y1 music player APK (Artist→Album navigation). |
| `--remove-apps` | Remove bloatware (`ApplicationGuide`, `BasicDreams`, …). |
| `--root` | Install `src/su/build/su` at `/system/xbin/su` (mode 06755). |
| `--all` | `--adb` + `--bluetooth` + `--music-apk` + `--remove-apps` + `--root`. Excludes `--avrcp`. |

Run `./apply.bash --help` for full flag detail. Patchers can also be run standalone — see [`src/patches/README.md`](src/patches/README.md).

## Diagnostics

Post-root tools for investigating AVRCP behaviour on hardware. None are invoked by the patch flow. Pre-req: `--root` flashed.

- **`@btlog` tap** — `src/btlog-dump/` (no-libc ARM ELF) + `tools/dual-capture.sh` (push + run + capture btlog & logcat) + `tools/btlog-parse.py` (decode framing). See [`src/btlog-dump/README.md`](src/btlog-dump/README.md).
- **Post-root probe** — `tools/probe-postroot.sh` + `tools/probe-postroot-device.sh`. Enumerates PIE base, MTK debug nodes, btsnoop paths, `getprop` keys, ptrace policy, abstract sockets. Re-run against any new `KNOWN_FIRMWARES` entry.
- **gdbserver attach to mtkbt** — `tools/install-gdbserver.sh` + `tools/attach-mtkbt-gdb.sh`. The installer fetches a pinned ARM 32-bit static `gdbserver` binary from AOSP prebuilts (~186 KB, sha256-verified) into `tools/gdbserver`. The attach script pushes it to `/data/local/tmp/`, attaches to the live mtkbt PID, computes the PIE base from `/proc/<pid>/maps`, and generates a gdb command file with breakpoints at the AVCTP-RX classifier (file offsets `0x6db7c` / `0x6dc36` / `0x6dc52`) plus the dispatcher arms (`0x515ca` / `0x51622`) — all translated to live addresses. Used to settle which event-code path PASSTHROUGH vs VENDOR_DEPENDENT inbound frames take.

Background and the failed alternatives these tools replace (`persist.bt.virtualsniff`, the G1/G2 xlog→logcat redirect): [`docs/INVESTIGATION.md`](docs/INVESTIGATION.md).

## Status

`--all` produces a working device: pairing, A2DP audio, AVRCP 1.0 PASSTHROUGH (play/pause/skip from car/headset), `--root`, and the `--music-apk` / `--remove-apps` / `--adb` flags all work.

**AVRCP 1.3 metadata over Bluetooth is working under `--avrcp`.** Peer Bluetooth Controllers (car head units, TVs, smart speakers) see all seven §5.3.4 element attributes (Title/Artist/Album/TrackNumber/TotalNumberOfTracks/Genre/PlayingTime), current play status with live position, and play-state edges from the Y1 in real time. The SDP record advertises AVRCP **1.3** over AVCTP **1.2**; `mtkbt`'s shipped command-handling layer is internally AVRCP 1.0 and rejects anything past `PASSTHROUGH` natively, so the trampoline chain in `libextavrcp_jni.so` synthesises the 1.3 responses directly. Implemented PDU set per [`docs/spec/AVRCP_SPEC_V13.pdf`](docs/spec/) (V13 + ESR07 errata):

| Spec § | PDU | Coverage |
|---|---|---|
| §5.1.1 | 0x10 GetCapabilities | full |
| §5.2.7 | 0x17 InformDisplayableCharacterSet | full |
| §5.2.8 | 0x18 InformBatteryStatusOfCT | full |
| §5.3.1 / §5.3.4 | 0x20 GetElementAttributes | all 7 attributes (Title/Artist/Album/TrackNumber/TotalNumberOfTracks/Genre/PlayingTime) in single-frame response |
| §5.4.1 | 0x30 GetPlayStatus | full, with live position via `clock_gettime(CLOCK_BOOTTIME)` |
| §5.4.2 | 0x31 RegisterNotification | INTERIM for events 0x01–0x07; CHANGED-on-edge for 0x01 (PLAYBACK_STATUS), 0x02 (TRACK_CHANGED), 0x03 (TRACK_REACHED_END, gated on natural end), 0x04 (TRACK_REACHED_START), 0x05 (PLAYBACK_POS, 1 s cadence while playing), 0x06 (BATT_STATUS, real bucket from `ACTION_BATTERY_CHANGED`) |
| §5.5 | 0x40 / 0x41 RequestContinuingResponse / Abort | explicit AV/C reject (we never fragment, so a CT shouldn't see these in valid flow) |
| §4.6.1 | PASS THROUGH (PLAY/PAUSE/STOP/FORWARD/BACKWARD/etc.) | discrete op_id routing per AV/C Panel Subunit Spec |

Compliance scorecard against the AVRCP ICS (Implementation Conformance Statement) Table 7 in [`docs/AVRCP13-COMPLIANCE-PLAN.md`](docs/AVRCP13-COMPLIANCE-PLAN.md) §2 — every mandatory row hits.

The breakthrough is a chain of binary trampolines patched into `libextavrcp_jni.so` that intercept inbound AVRCP commands and call the existing C response-builder functions in `libextavrcp.so` directly, bypassing the OEM's no-op Java AVRCP TG. Full architecture, calling conventions, and the ELF-segment-extension technique used to host code past the original LOAD #1 segment end are documented in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

`--bluetooth` covers only pairing-essential config edits — it does not modify SDP/AVRCP behavior; that's all under `--avrcp`.

Full investigation history including refuted hypotheses and the path to the current architecture: [`docs/INVESTIGATION.md`](docs/INVESTIGATION.md).

## Stock firmware manifest

Known stock firmwares recognised by `KNOWN_FIRMWARES` in the bash. Add a row (same five-field schema) to enrol a new build.

| Version | rom.zip (input) | system.img (raw, extracted) | boot.img (in zip; not consumed since v1.7.0) | Music APK basename in `app/` |
|---|---|---|---|---|
| **3.0.2** | `82657db82578a38c6f1877e02407127a` | `473991dadeb1a8c4d25902dee9ee362b` | `1f7920228a20c01ad274c61c94a8cf36` | `com.innioasis.y1_3.0.2.apk` |

Stock sizes (v3.0.2, the currently enrolled build): `rom.zip` 259,502,414 bytes; `system.img` 681,574,400 bytes (raw ext4 — auto-de-sparsed via `simg2img` if a build bundles a sparse one); `boot.img` 4,706,304 bytes.

## Requirements

- **Linux host**, Bash 4+, `sudo`. The patcher uses `mount -o loop` and GNU `sed -i` syntax — both Linux-only. macOS users would need a Linux VM (Lima, OrbStack, UTM) or a remote Linux shell.
- `git`, `unzip`, `md5sum`.
- Python 3.8+ with `venv` module. Patcher byte-level scripts are stdlib-only; `patch_y1_apk.py` needs `androguard`, which `tools/setup.sh` installs into `tools/python-venv/`. Java 11+ also required for `--music-apk` (apktool's smali assembler; apktool itself is downloaded by `patch_y1_apk.py` on first invocation).
- `tools/setup.sh` clones MTKClient (currently pinned to 2.1.4.1) into `tools/mtkclient/` and creates `tools/mtkclient/venv/` with its requirements. Override with `--mtkclient-dir <path>` or `MTKCLIENT_DIR` if you have it elsewhere.
- `simg2img` — only if the matched `KNOWN_FIRMWARES` build bundles a sparse `system.img` (the currently-enrolled v3.0.2 is raw). Install: `dnf install android-tools` (Fedora/RHEL via EPEL), `apt install android-sdk-libsparse-utils` (Debian/Ubuntu), `pacman -S android-tools` (Arch).
- For `--root` only: prebuilt `src/su/build/su`. Build via `cd src/su && make`. Toolchain: `dnf install -y epel-release && dnf install -y gcc-arm-linux-gnu binutils-arm-linux-gnu make` (Rocky/Alma/RHEL/Fedora) or the equivalent `gcc-arm-linux-gnueabi` package on Debian/Ubuntu.
- For `--avrcp` only: Android SDK + JDK 17+. Gradle is bootstrapped by the in-tree wrapper at `src/Y1MediaBridge/gradlew`. The repo's `tools/install-android-sdk.sh` auto-installs the SDK into `tools/android-sdk/` (~1.5 GB; idempotent, short-circuits on existing `ANDROID_HOME`). Manual install instructions in [`docs/ANDROID-SDK.md`](docs/ANDROID-SDK.md).

## Documentation

- [CHANGELOG.md](CHANGELOG.md) — version history (Keep a Changelog format)
- [docs/ANDROID-SDK.md](docs/ANDROID-SDK.md) — Android SDK install instructions (only needed for `--avrcp` / Y1MediaBridge build)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — AVRCP metadata proxy architecture: data-path diagram, trampoline chain (T1/T2/extended_T2/T4/T5/T_charset/T_battery/T_continuation/T6/T8/T9 + R1 + U1), response-builder calling conventions, ELF segment-extension technique, code-cave inventory. Read this first if working on the metadata pipeline.
- [docs/AVRCP13-COMPLIANCE-PLAN.md](docs/AVRCP13-COMPLIANCE-PLAN.md) — current ICS Table 7 coverage scorecard plus the staged path to closing remaining gaps (Phase F4 PlayerApplicationSettings is the only deferred phase)
- [docs/INVESTIGATION.md](docs/INVESTIGATION.md) — chronological AVRCP investigation history, refuted hypotheses, trace log
- [docs/PATCHES.md](docs/PATCHES.md) — per-patch byte-level reference (offsets, before/after bytes, rationale)

## Deployment notes

The patched music-player APK must be deployed directly to `/system/app/` on the device filesystem — **not** via `adb install` or PackageManager. The original META-INF signature block is retained (stale, not re-signed); it satisfies PackageManager's parseable-signature requirement, and signature verification is bypassed when deploying via the filesystem during boot. The bash's `--music-apk` flag handles this automatically. Manual ADB push:

```bash
adb root && adb remount
adb push com.innioasis.y1_<version>-patched.apk /system/app/com.innioasis.y1/com.innioasis.y1.apk
adb shell chmod 644 /system/app/com.innioasis.y1/com.innioasis.y1.apk
adb reboot
```

## Verified against

Innioasis Y1 — MTK MT6572 ARM, Android 4.2.2 (JDQ39), Dalvik VM API 17. Hardware-verified against the v3.0.2 build enrolled in [`KNOWN_FIRMWARES`](#stock-firmware-manifest); other builds need a manifest row added and may need patch-site offsets re-located if their stock MD5s diverge.

## Author

Sean Halpin ([github.com/SeanathanVT](https://github.com/SeanathanVT))

## License

GNU General Public License v3.0 (GPLv3) — see [LICENSE](LICENSE).
