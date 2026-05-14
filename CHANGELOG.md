# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/spec/v2.0.0.html). For full prose detail on any entry, see `git log`.

## [Unreleased]

### Changed
- `--bluetooth`'s `ro.bluetooth.class` now sets the Information service bit alongside Audio (Portable Audio Major/Minor unchanged). Stock value advertised only the Audio bit, which under-represents the device's TG-side metadata capability. Doesn't fix strict-CT metadata-pane gating on its own (verified — neither this nor a full Phone-class masquerade unblocked it), but is the more honest representation regardless.
- AVRCP `GetCapabilities(EventsSupported)` advertised set switched from `{0x01..0x08}` to `{0x01, 0x02, 0x05, 0x08, 0x09, 0x0a, 0x0b, 0x0c}`, mirroring what Pixel-as-TG sends. Events 0x09-0x0c are 1.4+ event IDs INTERIM-acked with zero/empty payload via the existing `libextavrcp.so` response builders that the JNI shim links but stock never invokes; no CHANGED ever fires (Y1 has one player, no Now Playing folder, no UID database). Trade-off: drops 0x03 TRACK_REACHED_END, 0x04 TRACK_REACHED_START, 0x06 BATT_STATUS_CHANGED, 0x07 SYSTEM_STATUS_CHANGED from the advertised set to stay at the 8-event cap. Targets strict CT metadata-pane render, which empirically gates on the 1.4 event IDs being advertised + acked even against a 1.3-declared TG.
- `T_charset` (PDU 0x17 InformDisplayableCharacterSet) now rejects with AV/C `NOT_IMPLEMENTED` via the existing UNKNOW_INDICATION path instead of acking via `inform_charsetset_rsp`. Spec-permissible (AVRCP 1.3 §5.2.7 is Optional); matches Pixel-as-TG behaviour. Y1's previous ACK stalled at least one strict CT for 3 seconds between InformDisplayableCharacterSet and the first RegisterNotification — the reject path lets the subscription burst land within milliseconds.
- `mtkbt`: M1 / M1b / M1c patches flip the AV/C ctype byte emitted for msg=544 RegisterNotification responses from `0x0D` (CHANGED) to `0x0F` (INTERIM) at three sites within fn `0x379e0` (files `0x37cca`, `0x37d3c`, `0x37dfc`). Stock mtkbt hardcoded CHANGED at all three sites, which was correct for stock JNI flow (msg=544 was only called for actual value changes); the v2.0 trampoline chain routes both initial INTERIM and proactive CHANGED through msg=544, so without these patches every RegisterNotification response went out as CHANGED-without-INTERIM, violating AVRCP 1.3 §6.7.1 and causing strict CTs to fall back to ~3 s polling indefinitely (metadata pane never rendered). The function has three CHANGED-writing branches; the inbound dispatch ladder routes different scenarios through different branches, so all three need flipping (an earlier single-site M1 patch had no observable effect on the wire — see Trace #35). Trade-off: T5 / T9 proactive edge emits also become INTERIM-on-wire; CTs treat repeated INTERIMs as fresh subscription confirmations per §6.7.1, so state refresh continues via the INTERIM payload.
- Pixel-mirror emit semantics in `libextavrcp_jni.so` T5 / T9 (see Trace #36). Drops the §6.7.1 per-subscription "single-shot CHANGED" gate clearing — once a CT subscribes to an event in a session, every subsequent value change emits a wire frame. Adds: NowPlayingContentChanged CHANGED in T5 (track-edge) + T9 (play-edge); PlaybackPositionChanged CHANGED in T5 (track-edge, carrying `file[780..783]` REV → host-order position). Mirrors Pixel-as-TG's exact wire-emit pattern from the `pixel4-bugreport` btsnoop capture: 1Hz position tick while playing, 3-frame burst on play/pause edge (PlaybackStatus + NowPlayingContent), 3-frame burst on track edge (NowPlayingContent + PlaybackPos + TrackChanged). Targets reliable metadata-pane render across the test matrix without requiring strict CTs to re-register between every value change.
- `y1-trampoline-state` schema extended from 20 → 21 bytes on disk (24 B in-memory, 4-B aligned). New byte `state[20]` holds `sub_now_playing_content` (event 0x09 subscription gate). Older state files degrade gracefully — short reads zero-extend in the memset'd in-memory buffer, and the first T8 0x09 INTERIM arm extends the file via `lseek + write` past EOF.

## [2.1.0] - 2026-05-13
AVRCP 1.3 metadata + control pipeline over Bluetooth. A peer Controller now sees full track metadata, live play status, and play-state changes from the Y1, and can drive Repeat / Shuffle from its own UI. Reference docs: [`docs/BT-COMPLIANCE.md`](docs/BT-COMPLIANCE.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/PATCHES.md`](docs/PATCHES.md). Investigation history: [`docs/INVESTIGATION.md`](docs/INVESTIGATION.md).

### Added
- AVRCP 1.3 metadata (Title / Artist / Album / Genre / TrackNumber / TotalNumberOfTracks / PlayingTime), with UTF-8 codepoint-safe text truncation.
- Live play status with millisecond-precision playhead, track-edge anchoring, end-of-track freeze + reset.
- Track-change notifications, including TRACK_REACHED_END (on natural end-of-track) and TRACK_REACHED_START.
- Battery-status notifications with bucketed change-on-edge semantics.
- Seek-bar propagation — the music app's in-UI seek lands on the CT's playhead immediately.
- Bidirectional Repeat / Shuffle. CT and Y1 UI stay in sync without navigating away and back.
- Discrete PASSTHROUGH routing (PLAY / PAUSE / STOP / NEXT / PREVIOUS) for CTs that don't tolerate toggle behaviour, plus PLAY-while-playing → pause-toggle for non-spec CTs.
- A2DP stream survives pauses — AudioFlinger silence-timeout no longer tears down the AVDTP source.
- Per-subscription notification gating (AVRCP §6.7.1) — one INTERIM + one CHANGED per registration, matching spec-compliant TG semantics.
- `Y1Bridge` Android service satisfies MtkBt's `bindService(MediaPlaybackService)` and answers synchronous queries from the music-app-owned state file.
- Spec-compliant `GetElementAttributes` response shape — TG emits exactly the requested attribute IDs in the requested order; unsupported IDs emit with length 0.

### Changed
- GitHub repository renamed `y1-mods` → `koensayr`.
- `--all` now includes `--avrcp`. The AVRCP 1.3 pipeline is spec-mature; the prebuild requirement (`./gradlew assembleDebug` in `src/Y1Bridge/`) mirrors `--root`'s `make` in `src/su/`.
- `tools/release.sh --push` now pushes the current branch instead of hardcoded `main`. Bails with a clear error if invoked from a detached HEAD.

### Removed
- Legacy SDP-only byte-patch attempts (regressed PASSTHROUGH without delivering metadata).
- Legacy adbd byte-patch attempts (superseded by `src/su/`).

## [2.0.0] - 2026-05-04

Foundational rebrand + diagnostic tooling release. The `--avrcp` flag is documented as known-broken pending the user-space proxy work that becomes the [Unreleased] pipeline.

### Added
- `src/btlog-dump/` — minimal ARM ELF that taps mtkbt's `@btlog` socket; pulls AVRCP / AVCTP / L2CAP traces invisible to `logcat`.
- `tools/dual-capture.sh` + `tools/btlog-parse.py` — captures and decodes the btlog stream alongside `logcat`.
- `tools/probe-postroot.sh` — one-shot device probe enumerating mtkbt internals, btsnoop paths, ptrace policy, abstract sockets.
- `tools/release.sh` — release helper (version bump, CHANGELOG rewrite, tag).
- `tools/install-android-sdk.sh` — auto-installs Android SDK for the `Y1Bridge` build.
- `LICENSE` — canonical GPLv3 text (project has claimed GPLv3 since v1.0.8).

### Changed
- Project rebrand `Innioasis Y1 Firmware Fixes` → `Koensayr`. GitHub repo name stays for discoverability.
- Orchestration script renamed `innioasis-y1-fixes.bash` → `apply.bash`.
- `--all` redefined as `--adb` + `--bluetooth` + `--music-apk` + `--remove-apps` + `--root`. `--avrcp` excluded.
- `--avrcp` documented as known-broken on the byte-patch path; runs only on explicit opt-in with a startup warning.
- `--bluetooth` no longer sets `persist.bluetooth.avrcpversion` (mtkbt can't deliver the claimed version). Pairing-essential edits remain.
- `--artifacts-dir` is optional; defaults to `./staging/` inside the repo. `cp rom.zip staging/` is enough.
- Project is now unambiguously Linux-only. macOS support removed (uses `mount -o loop` and GNU `sed -i`).
- README, sub-READMEs, and `apply.bash --help` rewritten end to end for the new state.

### Fixed
- Defensive hardening across `apply.bash` and helper scripts: pre-checks for `python3` / `sudo` / git config, exit-code checks on `simg2img` / `cp` / `mount` / `umount` / MTKClient flash, cleanup trap unmounts on EXIT, `--help` no longer triggers side effects in tools that previously ran setup work on it.
- `--remove-apps` now actually removes apps (glob expansion was suppressed by quoting for the project's entire history).
- Patcher `OUTPUT_MD5` mismatch now exits non-zero (was silently exit 0).
- `tools/install-android-sdk.sh` license accept no longer fails silently under `set -o pipefail` (SIGPIPE on `yes`); partial-state downloads recover cleanly across re-runs.
- `tools/setup.sh` partial-state bug — incomplete venvs are detected via a marker file and retried rather than appearing complete.

## [1.10.0] - 2026-05-03

### Added
- `tools/setup.sh` — clones MTKClient at a pinned ref, builds the patcher's Python venv. Idempotent.
- `--mtkclient-dir` / `--python-venv` flags + `MTKCLIENT_DIR` env var to override the in-tree tooling.

### Changed
- Bash no longer assumes `/opt/mtkclient-2.1.4.1` paths. Resolution order: flag → env var → `tools/` default.

### Fixed
- `src/Y1MediaBridge/` missing `local.properties` ignore + missing Gradle wrapper that prevented `./gradlew assembleDebug` from running.

## [1.9.1] - 2026-05-03

### Fixed
- Switch `Y1MediaBridge` build target from `assembleRelease` to `assembleDebug` (avoids `lintVitalReportRelease` requiring a configured SDK path; both targets produce structurally identical APKs here).

## [1.9.0] - 2026-05-03

### Changed
- `--avrcp` builds `Y1MediaBridge.apk` from in-tree source via Gradle. Previously expected a pre-staged APK.
- `rom.zip` is the only required staged artifact.

## [1.8.x] - 2026-05-03

### Changed
- Monorepo layout: `su/` → `src/su/`; byte/smali patchers → `src/patches/`; `Y1MediaBridge` imported as `src/Y1MediaBridge/`.
- `apply.bash` `show_help` and in-source comments trimmed to single-screen output. Authoritative detail moved to README + docs.

### Added
- `CHANGELOG.md` (this file).
- `docs/PATCHES.md` — per-patch byte-level reference.

## [1.8.0] - 2026-05-03

### Added
- `--root` flag (current form): installs a minimal setuid-root `/system/xbin/su`. Stock `/sbin/adbd` stays untouched. `adb shell /system/xbin/su` gives root.
- `src/su/` — ~900-byte direct-syscall ARM-EABI ELF, no libc, no manager APK.

## [1.7.0] - 2026-05-03

### Removed
- Previous `--root` flag (boot.img `adbd` byte-patch). Hardware testing produced "device offline" — patched adbd brought up the USB endpoint but never completed the ADB handshake.

## [1.6.0] - 2026-05-03

### Changed
- Accept the official OTA `rom.zip` as the primary firmware input. Bash MD5-validates against `KNOWN_FIRMWARES`, then extracts what each flag needs.

## [1.5.0] - 2026-05-03

### Changed
- Stock-firmware MD5 validation against a `KNOWN_FIRMWARES` manifest (version, system.img, boot.img, rom.zip, music-APK basename). Replaces the previous hardcoded version constant.

## [1.4.x] - 2026-05-03

### Changed
- `--avrcp` and `--music-apk` extract stock binaries from the mounted `system.img`, patch in place, and write back. Only `rom.zip` (and the `Y1MediaBridge.apk` build output) need staging.
- Sparse-`system.img` auto-detection via `simg2img`.

## [1.3.x] - 2026-05-03

### Changed
- Initial boot.img-based `--root` (later superseded by the setuid-su approach in 1.8.0). Direct cpio mutation in pure Python; no shell-side `dd` / `mkbootimg`.

## [1.2.x] - 2026-04-26 → 2026-05-01

### Added
- Initial byte-patcher trio: `patch_mtkbt.py`, `patch_mtkbt_odex.py`, `patch_libextavrcp_jni.py`. Legacy SDP-shape byte-patch attempt (later determined inadequate and removed in 2.0.0).

## [1.1.x] - 2026-04-26

### Added
- `--root` flag (ramdisk-based; broke at 1.2.0, reintroduced differently at 1.3.0, broken again, finally reworked at 1.8.0).

## [1.0.x] - 2026-04-23 → 2026-04-25

### Added
- Initial release: Artist→Album navigation patch on the music app, Bluetooth pairing config (audio.conf / auto_pairing.conf / blacklist.conf / build.prop), preinstalled-bloatware removal, system patch dispatch via `apply.bash` flags.
