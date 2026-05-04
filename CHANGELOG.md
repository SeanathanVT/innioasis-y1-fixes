# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version reflects `innioasis-y1-fixes.bash`'s `# Version:` header; patch-script-only
changes are grouped under the bash version that was current at the time. For full
prose detail on any entry, see `git log` (commits are 1:1 with these bullets).

## [Unreleased]

### Fixed
- `src/patches/patch_y1_apk.py`: silence androguard 4.x's loguru output. The existing `logging.getLogger("androguard").setLevel(logging.ERROR)` line only suppresses the stdlib `logging` channel; androguard 4.0 switched to [loguru](https://github.com/Delgan/loguru) for its own logging, which ignores stdlib config. Result: a flood of `androguard | INFO`-style lines on every APK parse. Added a try/except `from loguru import logger; logger.disable("androguard")` block at module load (preventive — only if loguru is available transitively) and again immediately before the `from androguard.core.apk import APK` inside `get_apk_info` (just-in-time — covers the case where the module-load attempt hit ImportError because androguard hadn't been pulled in yet). Both stdlib + loguru channels now silenced.

### Changed
- **Bump AGP 8.7.3 → 9.2.0** in `src/Y1MediaBridge/build.gradle`. AGP 9.2.0 is the latest stable per Google's maven repository; 8.7.3 was from late 2024 and ~5 minor versions behind. AGP 9.x removes some long-deprecated DSL methods. Modernized `app/build.gradle` to match: `compileSdkVersion 34` → `compileSdk 34`, `minSdkVersion 17` → `minSdk 17`, `targetSdkVersion 17` → `targetSdk 17`. The `*Version` forms have been deprecated since AGP 7.x; the new property forms have been the canonical syntax for years and work on AGP 8.x too.
- **Bump cmdline-tools build 11076708 → 14742923** in `tools/install-android-sdk.sh`. The 11076708 pin was from August 2023; 14742923 is the latest published by Google. Verified that both the linux and mac variants of the new build are reachable at `dl.google.com/android/repository/`.
- Bake `--stop` into the documented `./gradlew assembleDebug` invocations (top-level README Quick start, `src/Y1MediaBridge/README.md` Build section). Gradle's build daemon caches the JVM it started with, so a `JAVA_HOME` change between builds doesn't take effect until the daemon restarts — and the resulting `[JAVA_COMPILER]` failure looks identical to a real toolchain problem. `./gradlew --stop && ./gradlew assembleDebug` always-stops the daemon first; cheap and prevents the most common newcomer-tripping error.
- Walk back the "JDK 22+ likely to fail" warning added in `c794239`. Empirical test on Fedora 43 with JDK 25 + AGP 8.7.3 + Gradle 9.5.0 builds cleanly. The original failure that motivated the warning turned out to be a stale gradle daemon (started with a JRE-only JDK 25, kept alive across `JAVA_HOME` changes) — not an AGP-vs-JDK incompatibility. `tools/install-android-sdk.sh` no longer warns on JDK > 21; only the JDK ≥ 17 floor stays. `docs/ANDROID-SDK.md` and `src/Y1MediaBridge/README.md` updated accordingly: confirmed working with JDK 17, 21, 25.
- `docs/ANDROID-SDK.md` adds a "Gotcha — gradle daemon caching" subsection: gradle keeps its build daemon alive across invocations, so changing `JAVA_HOME` after a build has run leaves the cached daemon on the old JVM (and reproduces the same `[JAVA_COMPILER]` error). Run `./gradlew --stop` before rebuilding when changing JDKs. Includes pointer to `./gradlew --version` for inspecting the daemon JVM path.

### Fixed
- `tools/install-android-sdk.sh`: license-accept step was failing silently — two compounding bugs. (1) `>/dev/null` on the original `yes | sdkmanager --licenses >/dev/null` swallowed any sdkmanager error, so the failure produced no diagnostic. (2) Even after removing the redirect, the line still failed because `yes | sdkmanager` gets SIGPIPE'd: when sdkmanager exits cleanly after accepting licenses, the still-running `yes` gets SIGPIPE and exits with status 141; `set -o pipefail` then propagates that 141 as the pipe's exit status even though sdkmanager itself succeeded ("All SDK package licenses accepted."). Switched to process substitution: `sdkmanager --licenses < <(yes)`. Process substitution runs `yes` in a background subshell whose exit isn't part of the foreground command's accounting, so SIGPIPE on `yes` no longer affects the script's exit. Both `sdkmanager` invocations (`--licenses` and `--install`) also now wrapped in explicit `if !` blocks that print a useful error including the manual-debug invocation and current `java -version`.
- `tools/install-android-sdk.sh`: download/unpack step wasn't fully idempotent — partial state from a prior failed run could survive across re-runs and break `mv` / `unzip`. Restructured: skip download/unpack entirely if `cmdline-tools/latest/bin/sdkmanager` already exists; otherwise wipe any half-extracted `cmdline-tools/` first and use `unzip -o` to overwrite.
- `tools/setup.sh`: drop `--quiet` from the four `pip install` invocations. With it set, pip suppressed all output during venv provisioning; the script appeared to hang for 1–3 minutes (mtkclient's `requirements.txt` pulls a non-trivial set of native deps). User-reported as "freezes". Pip's default progress is informative enough; if needed back, prefer `--progress-bar off` over `--quiet`.
- `tools/setup.sh`: pinned MTKClient ref was `2.1.4.1`; the actual upstream tag is `v2.1.4.1` (with the `v` prefix). Fresh runs were failing at `git checkout 2.1.4.1` with `pathspec did not match any file(s) known to git`. Pin updated.
- `tools/setup.sh`: same partial-state bug as `install-android-sdk.sh` — if `git clone` succeeded but `git checkout` failed (e.g. on the bad ref above), `tools/mtkclient/` existed on disk so a re-run silently skipped the clone *and* the checkout, leaving mtkclient at HEAD-of-main. Restructured: clone is gated by directory presence, but the checkout always runs (idempotent — `git checkout` to current ref is a no-op). If the ref isn't in the local clone, `git fetch --tags` first; if it still doesn't resolve, bail with `git ls-remote` instructions. Re-running heals.
- `tools/install-android-sdk.sh`: previously short-circuited the **entire** script (including the `local.properties` write) when `tools/android-sdk/` was already populated from a prior run. Net effect: a re-run or recovery from a partial first run never wired Gradle to the SDK, so `./gradlew assembleDebug` kept failing with `SDK location not found`. Restructured: download/install is the only step gated by the existing-SDK check; `local.properties` and the new env-file write **always** run, healing missing config.
- `tools/install-android-sdk.sh`: also generate `tools/android-sdk-env.sh` — a sourceable file that exports `ANDROID_HOME` and adds `cmdline-tools/latest/bin` + `platform-tools` to `PATH`. Lets users run `adb` / `sdkmanager` from their shell with `source tools/android-sdk-env.sh` (Gradle itself doesn't need this — it reads `local.properties`). Fixes the user-reported "script doesn't export ANDROID_HOME" gap. `.gitignore` updated to exclude the env file (per-machine, contains absolute paths).

### Added
- `tools/install-android-sdk.sh` — auto-installer for the Android SDK (Linux/macOS only). Detects existing `$ANDROID_HOME` and short-circuits; otherwise downloads Google's pinned commandline-tools archive (build `11076708`), accepts licenses (`yes | sdkmanager --licenses`), installs `platforms;android-34` + `build-tools;34.0.0` + `platform-tools`, and writes `sdk.dir=…` into `src/Y1MediaBridge/local.properties` so Gradle finds the SDK without `ANDROID_HOME` in your shell. Bails clearly if JDK 17+ is missing. Disk ~1.5–2 GB, network ~1.7 GB. Idempotent: re-runnable, pin-bumpable via `CMDLINE_TOOLS_BUILD` at the top.

### Changed
- `tools/setup.sh` post-completion message now points at `install-android-sdk.sh` for users who'll use `--avrcp`.
- `docs/ANDROID-SDK.md` rewritten to lead with the auto-installer; the per-platform manual recipes remain as the fallback (for Windows, supply-chain-restricted environments, or users who'd rather configure a system-wide install).
- README Quick start now includes `./tools/install-android-sdk.sh` as a sibling step to `./tools/setup.sh`. Requirements bullet for `--avrcp` updated.

### Documentation
- Add `docs/ANDROID-SDK.md` — platform-specific Android SDK install instructions for Linux (Rocky/Alma/RHEL/Fedora/Debian/Ubuntu/Arch), macOS (Homebrew + manual), and Windows (Android Studio + cmdline-tools + WSL2 note). Covers verifying an existing install, JDK 17+ requirement, license acceptance, and how to bump the SDK pins if `compileSdk`/AGP change. README Requirements bullet for `--avrcp` now points there; Documentation section indexes it alongside `docs/PATCHES.md` / `docs/DEX.md`.

## [1.10.0] - 2026-05-03

### Added
- `tools/setup.sh` — idempotent: clones MTKClient at a pinned ref (currently 2.1.4.1) into `tools/mtkclient/`, creates `tools/mtkclient/venv/` with its `requirements.txt`, and creates `tools/python-venv/` with the patcher Python deps from `tools/python-requirements.txt` (currently `androguard>=3.3.0,<5`). Re-runnable; skips already-done work.
- `tools/python-requirements.txt` — pinned patcher Python deps. Update + bump versions deliberately.
- `--mtkclient-dir <path>` flag on the bash. Override the default `tools/mtkclient/` location. Also honoured via the `MTKCLIENT_DIR` env var.
- `--python-venv <path>` flag on the bash. Override the default `tools/python-venv/` location.

### Changed
- **Bash no longer assumes `/opt/mtkclient-2.1.4.1` and `/opt/venv/mtkclient` exist.** The previous hardcoded paths were specific to one developer's machine. New `resolve_mtkclient_dir` and `resolve_python_venv` helpers do precedence-ordered lookup: explicit flag → env var → `tools/` default. If nothing resolves, the bash bails with a clear "run `tools/setup.sh`" pointer.
- MTKClient venv is now expected at `${PATH_MTKCLIENT}/venv/` (consistent with `tools/setup.sh`'s output). Previous `/opt/venv/mtkclient/` sibling layout dropped.
- `patch_in_place_y1_apk` activates the patcher venv inside its subshell so the activation is scoped to that single invocation. The other byte patchers (stdlib-only) continue to run against whatever `python3` is on PATH.

### Fixed
- Add `local.properties` to `src/Y1MediaBridge/.gitignore`. AGP looks for `sdk.dir=...` in this file as one of two ways to locate the Android SDK (the other being the `ANDROID_HOME` env var); it's per-machine and conventionally untracked. The original `.gitignore` was missing it.
- Commit the Gradle wrapper into `src/Y1MediaBridge/` (`gradlew`, `gradlew.bat`, `gradle/wrapper/gradle-wrapper.jar`). The standalone Y1MediaBridge repo's `.gitignore` had explicit ignore rules for these files (overriding its own earlier `!gradle-wrapper.jar` exception), so they never landed in the subtree merge — `./gradlew assembleDebug` was failing with `No such file or directory`. Wrapper now in place; pinned to Gradle 9.5.0 per `gradle-wrapper.properties`. Y1MediaBridge `.gitignore` cleaned up to remove the bad ignore rules.

### Documentation
- README Quick start: now opens with `./tools/setup.sh` for one-time tooling provisioning, then walks through the per-component build steps. Notes that `rom.zip` is the only required artifact and that `--mtkclient-dir` / `--python-venv` / `MTKCLIENT_DIR` are available for users with their own toolchain installs.
- README Requirements: dropped the "mtkclient 2.1.4.1 at /opt/..." line (now covered by `tools/setup.sh`); added a line about the Android SDK being needed only for `--avrcp`.
- Y1MediaBridge README's Toolchain note: "Gradle 8.11.1 wrapper" → "Gradle 9.5.0 wrapper" (was stale; the upstream had bumped to 9.5.0 just before the subtree import).

## [1.9.1] - 2026-05-03

### Fixed
- Switch the Y1MediaBridge build invocation from `assembleRelease` to `assembleDebug`. AGP 8.7.3 wires `lintVitalReportRelease` into the release-assembly chain, and that task fails with `SDK location not found` unless `local.properties` (`sdk.dir=...`) is configured. `assembleDebug` skips it. The two APKs are structurally identical here (`minifyEnabled false` on both; both signed with the debug keystore per `app/build.gradle`'s `signingConfig signingConfigs.debug`). Debug also leaves `debuggable=true` in the manifest, which is useful for a research device. Bash now references `app/build/outputs/apk/debug/app-debug.apk`; `--avrcp` help text and the missing-prebuilt error point at `assembleDebug`. README Quick start, flag table, and `src/Y1MediaBridge/README.md` Build section updated to match.

## [1.9.0] - 2026-05-03

### Changed
- **`rom.zip` is now the only required artifact in `--artifacts-dir`.** `--avrcp` no longer expects a pre-staged `Y1MediaBridge.apk`; the bash references `src/Y1MediaBridge/app/build/outputs/apk/release/app-release.apk` (the gradle release-build output) directly and copies it into the system.img mount renamed to `Y1MediaBridge.apk` (mode 644, root:root). Build once via `cd src/Y1MediaBridge && ./gradlew assembleRelease` — symmetric with `--root`'s `cd src/su && make` requirement. If the prebuilt is missing, `--avrcp` bails with a clear instruction.
- `app/build.gradle` uses `signingConfig signingConfigs.debug` for the release build, so `./gradlew assembleRelease` produces a signed APK out of the box (no keystore setup required).

### Documentation
- `src/Y1MediaBridge/` is now the canonical source for the Y1MediaBridge subproject; the standalone external repo it was originally subtree-imported from is being deleted. Top-level README's Layout entry no longer mentions the subtree import; Quick start now describes building `Y1MediaBridge.apk` from in-tree source via Gradle. Bash echo no longer says "externally built".
- Harmonize subproject READMEs to follow the root README's formatting conventions:
  - `src/Y1MediaBridge/README.md`: drop `(on macOS host)` from the Build header (Gradle is cross-platform); soften the all-caps `(MUST be system app for READ_LOGS)` header to `Install as system app` with the rationale in the body; rename `Test — end-to-end` → `End-to-end test`; relocate the `Changes` section from top (between Architecture and Build) to bottom (after Reverse engineering notes), with sentence-case version subheadings; add a `See also` section pointing at top-level docs.
  - `src/su/README.md` and `src/patches/README.md`: promote inline closing-paragraph cross-references to a dedicated `See also` section for parity with the Y1MediaBridge structure.
- Decouple top-level docs from "v3.0.2 only" framing: README tagline now references the `KNOWN_FIRMWARES` manifest as the compatibility source-of-truth ("add a row to support a new build"); Quick start describes the rom.zip + two build trees workflow; the bash header comment was reframed the same way; Verified-against now distinguishes "the device this is for (Innioasis Y1 hardware)" from "the build that's currently enrolled and verified (v3.0.2)". Patcher script docstrings keep their "verified on 3.0.2" notes — those are factual development records.

## [1.8.3] - 2026-05-03

### Changed
- Drop the in-file version-history block from `innioasis-y1-fixes.bash` (~30 lines); `git log` and `CHANGELOG.md` are authoritative.
- `show_help()` reduced from 63 lines to 20 (single-screen output): one-line description per flag, one example, pointer to README.md / docs/PATCHES.md for details.
- Trim function-doc and inline rationale comments throughout (110 comment lines → 45). Comments now retained only where the *why* is non-obvious from the code (e.g., `FLAG_ANY_SYSTEM_PATCH` separateness for future flag flexibility).
- No functional change.

### Documentation
- README.md restructured as a quick-start with monorepo layout, flag table, and pointers to the docs files. 439 → 126 lines.
- Add `CHANGELOG.md` (this file, Keep a Changelog format).
- Move per-patch byte-level reference into `docs/PATCHES.md`.
- Move DEX-level analysis for `patch_y1_apk.py` into `docs/DEX.md`.

## [1.8.2] - 2026-05-03

### Changed
- Move the seven `patch_*.py` scripts (`patch_mtkbt.py`, `patch_mtkbt_odex.py`, `patch_libextavrcp.py`, `patch_libextavrcp_jni.py`, `patch_y1_apk.py`, `patch_adbd.py`, `patch_bootimg.py`) into `src/patches/`.
- Bash dispatch updated: `patch_in_place_bytes` and `patch_in_place_y1_apk` now reference `src/patches/...`; `patch_y1_apk.py` runs from `src/patches/` so its `output/` and `_patch_workdir/` land there.

### Added
- Y1MediaBridge imported as `src/Y1MediaBridge/` via `git subtree add` (full Y1MediaBridge history grafted under that prefix). Source tree only — `Y1MediaBridge.apk` is still externally-built and staged in `--artifacts-dir` for `--avrcp`.

## [1.8.1] - 2026-05-03

### Changed
- Move `su/` → `src/su/` in anticipation of the monorepo layout. Bash references `${PATH_SCRIPT_DIR}/src/su/build/su`; build instruction in `--root` help and missing-prebuilt error become `cd src/su && make`.

## [1.8.0] - 2026-05-03

### Added
- `--root` flag (replaces v1.3.x–v1.6.0 approach): installs a minimal setuid-root `su` binary at `/system/xbin/su` (mode 06755, root:root). Stock `/sbin/adbd` stays untouched, so the ADB protocol handshake comes up cleanly; root is obtained post-flash by `adb shell /system/xbin/su`.
- `su/su.c` (~80 lines) and `su/start.S` (~10 lines) — direct ARM-EABI syscalls, no libc, no manager APK. Output is a ~900-byte statically-linked ARMv7 ELF with no dynamic dependencies.
- `su/Makefile` for the `arm-linux-gnu-gcc` cross-build (EPEL `gcc-arm-linux-gnu` toolchain).

### Changed
- `--root` is now system.img-only (no boot.img extraction, no ramdisk repack). Re-added to `--all`.

## [1.7.0] - 2026-05-03

### Removed
- `--root` flag entirely. The H1/H2/H3 byte patches in `/sbin/adbd` (both NOP-the-blx and arg-zero revisions) caused "device offline" on hardware in flash testing — adbd starts and the USB endpoint enumerates, but the ADB protocol handshake never completes. Static analysis found no `getuid()` gate, no uid==2000 compare, no obvious capability check; failure mode invisible without on-device visibility (which we lose the moment we ship a broken adbd).
- Boot.img extraction, `patch_bootimg` invocation, and boot.img mtkclient flash dropped from the bash. `patch_adbd.py` and `patch_bootimg.py` kept in the tree as historical record.

### Notes
- Revised H1/H2/H3 from "NOP the blx calls" to "change argument values from 2000 to 0" (kept all syscalls + bionic wrappers intact). Stock adbd MD5 unchanged; new patched MD5 `9eeb6b3bef1bef19b132936cc3b0b230` (was `ccebb66b25200f7e154ec23eb79ea9b4`). Both revisions broke ADB on hardware; arg-zero diagnosis preserved in `patch_adbd.py` docstring.
- Fresh test log (peer `38:42:0B:38:A3:3E`) flagged a new finding: `MSG_ID_BT_AVRCP_CONNECT_CNF result:4096` (= `0x1000`). Non-zero result on a successfully-connected channel suggests mtkbt is reporting "accepted-but-degraded" — strongest static lead for the post-root pass.
- Trace #7 (libbluetooth_*.so audit): all four `libbluetooth*.so` libs are HCI/transport-only. Combined `strings` search returned zero hits for AVRCP/AVCTP/profile/capability/notif/metadata/cardinal. Cardinality:0 gate is unambiguously inside `mtkbt`.

## [1.6.0] - 2026-05-03

### Changed
- Take the official OTA `rom.zip` as the primary firmware input (replaces v1.5.0 separately-staged `boot.img`/`system.img`). Users now stage just `rom.zip` + `Y1MediaBridge.apk`. Bash MD5-validates `rom.zip` against `KNOWN_FIRMWARES`, derives the firmware version from the match, then `unzip -j -o`'s only the inner files needed by the active flags.
- `unzip` is now a hard dependency.

## [1.5.0] - 2026-05-03

### Changed
- Replace the hardcoded `VERSION_FIRMWARE="3.0.2"` constant with stock-firmware MD5 validation. New `KNOWN_FIRMWARES` manifest holds (version, system.img md5, boot.img md5, rom.zip md5, music-APK basename) tuples. Staged `system.img` (post-simg2img if sparse) and `boot.img` (when `--root`) are MD5-validated against the manifest before any patch step runs. Cross-platform MD5 helper prefers `md5sum` (Linux), falls back to `md5 -q` (macOS).
- v3.0.2 enrolled as the only known build (system.img raw `473991dadeb1a8c4d25902dee9ee362b`, boot.img `1f7920228a20c01ad274c61c94a8cf36`, rom.zip `82657db82578a38c6f1877e02407127a`).

## [1.4.1] - 2026-05-03

### Added
- Auto-handle sparse `system.img` via `simg2img` (detected by `file` output or sparse magic `0xed26ff3a`). Bails with install instructions for Debian/Ubuntu, Arch, Fedora, RHEL/Rocky/Alma 8+, and macOS if `simg2img` is missing.

## [1.4.0] - 2026-05-03

### Changed
- Drop the pre-staged-artifacts requirement. `--avrcp` and `--music-apk` now extract stock binaries from the mounted `system.img`, run the corresponding `patch_*.py`, and write back in-place. Only `Y1MediaBridge.apk` (externally-built) and `boot.img` (for `--root`) need staging.
- New helpers `patch_in_place_bytes <mount-rel> <patch-script> [mode]` and `patch_in_place_y1_apk <mount-rel>`.
- Idempotent: re-running detects already-patched binaries and skips write-back.

### Added
- `patch_adbd.py` — H1/H2/H3 NOP patches for adbd's drop_privileges block (later revised to arg-zero in 1.7.0; both ultimately abandoned).
- `patch_bootimg.py` extracts `/sbin/adbd` from the cpio, applies `patch_adbd.patch_bytes()`, and writes back in-place.

## [1.3.2] - 2026-05-03

### Changed
- No functional bash changes; reflects `patch_bootimg.py` absorbing `patch_adbd.py`.
- `--root` help text: `adb root` no longer flagged as harmful (the v1.3.1 warning was specific to the property-only patcher).

## [1.3.1] - 2026-05-03

### Changed
- `--root` no longer touches `system.img` (skip copy/mount/patch/unmount/flash and the sudo prompt unless a system-affecting flag is also set). Previous v1.3.0 flow re-flashed an unmodified `system.img` for `--root`-only invocations — pure cycle waste.
- Drop `service.adb.root=1` from `patch_bootimg.py`'s `_DEFAULT_PROP_EDITS` based on the (incorrect, same-day-disproven) hypothesis that `ro.secure=0` would make adbd skip the privilege drop.

## [1.3.0] - 2026-05-03

### Added
- Reintroduce `--root` flag, backed by a new `patch_bootimg.py` (pure-Python in-place cpio mutation; no shell-side `dd`/`cpio`/`mkbootimg`). Patches `default.prop` in-place inside the gzipped cpio; repacks Android boot.img header with recomputed SHA1 and original load addresses. Default.prop edits: `ro.secure=0`, `ro.debuggable=1`, `ro.adb.secure=0`, `service.adb.root=1`.

### Notes (during 1.3.0 lifetime, before 1.4.0)
- 2026-05-02 — Add E8 to `patch_mtkbt.py` (NOP `bge #0x30688` at `0x3065e` in fn `0x3060c`, op_code=4 dispatcher slot 0). Forces classification through the AVRCP 1.3/1.4 init path. Tested 2026-05-02 and observed inert (gate is upstream of the dispatcher table); kept as a verified-correct probe.
- 2026-05-02 — Remove E5 / E7a / E7b from `patch_mtkbt.py` (empirically inert across car / Sonos Roam / Samsung TV).
- 2026-05-02 — Add G1/G2 diagnostic instrumentation patches (xlog→logcat redirect at `0x675c0` / `0xb408`). Reverted same-day after hardware test: SIGSEGV at NULL fmt pointer; bionic API 17 doesn't NULL-check tag arg.
- 2026-05-02 — Re-add G1 with NULL guard. Reverted: BT framework couldn't enable (`bt_sendmsg` ENOENT — abstract socket missing). Blanket xlog→logcat redirect closed as too fragile.
- 2026-05-02 — Audit pass over all in-repo patch script documentation. End state: cardinality:0 persists across three 1.4 controllers despite all SDP/feature/dispatcher patches being on-wire.

## [1.2.2] - 2026-04-30

### Changed
- No functional bash changes; reflects `patch_mtkbt.py` update to include AVCTP 1.0→1.3 patches (B1-B3).

### Notes (during 1.2.x lifetime)
- 2026-05-01 — Add E5 patch to `patch_mtkbt.py` (1-byte `BNE → B` at `0x309ed`). Forces all op_code=4 dispatch through the 1.3/1.4 init path.
- 2026-05-01 — Harmonize all four byte-patch scripts onto a single `PATCHES = [{name, offset, before, after}, ...]` template with shared `verify`/`print_results` helpers and uniform MD5 status output.
- 2026-05-01 — Add E3/E4 SupportedFeatures patches: TG SupportedFeatures bitmask `0x01 → 0x33` at `0x0eba5b` (Group 2 served), `0x21 → 0x33` at `0x0eba4e` (Group 1 defense-in-depth) — Cat1 + Cat2 + PAS + GroupNav.
- 2026-05-01 — Reverted E1 and E2 (added and removed same session). Both were incorrect: E1 bypassed a legitimate state guard; E2 routed 1.3/1.4 cars to the AVRCP 1.0 path.
- 2026-04-30 — Investigate persistent `tg_feature:0 ct_feature:0` post-D1. Confirmed `tg_feature` is logged but not used for functional gating. Root cause of cardinality:0: C3a/C3b in `patch_libextavrcp_jni.py` (GetCapabilities event count cap 13→14).
- 2026-04-30 — Add D1 patch: NOP the `BNE 0x38C76` at `0x38C6C`. Without this, the AVRCP TG SDP struct is built but never linked into mtkbt's live registry.
- 2026-04-30 — Add B1-B3 (AVCTP 1.0→1.3) patches to `patch_mtkbt.py`.
- 2026-04-30 — Regression analysis confirms three `AttrID=0x0009` (ProfileDescList) entries; restored and upgraded all to AVRCP 1.4.
- 2026-04-29 — Full Prong C (JNI/native) audit; no new binary patch required for JNI layer. Add A1 (`MOVW r7,#0x0301→#0x0401` at `0x38BFC`).
- 2026-04-27 — Rename `patch_odex.py` → `patch_mtkbt_odex.py`; add F2 (reset `sPlayServiceInterface` in `BluetoothAvrcpService.disable()`).
- 2026-04-27 — All patch scripts write output to `output/` subdirectory; `_patch_workdir` cleaned up after `patch_y1_apk.py` run.

## [1.2.1] - 2026-04-26

### Added
- Deploy `libextavrcp.so.patched` via `--avrcp`.
- New `patch_libextavrcp.py` (libextavrcp.so AVRCP 1.4 version constant). Renamed `patch_so.py` → `patch_libextavrcp_jni.py`.

## [1.2.0] - 2026-04-26

### Removed
- `--root` flag and boot.img handling (broken).

## [1.1.3] - 2026-04-26

### Changed
- Prompt for sudo credentials upfront; keep ticket alive for script duration to prevent mid-execution prompts.

## [1.1.2] - 2026-04-26

### Fixed
- `--root`: use `sudo cpio` to preserve device nodes; add `ro.adb.secure=0` and `service.adb.root=1` to ramdisk `default.prop`; remove size mismatch failure.

## [1.1.1] - 2026-04-26

### Fixed
- macOS compatibility: replace `stat -c%s` with `wc -c` for file size.

## [1.1.0] - 2026-04-26

### Added
- `--root` flag to patch boot.img ramdisk for ADB root access.
- `patch_mtkbt.py`, `patch_odex.py`, `patch_so.py` — all three BT binaries patched for AVRCP 1.4.

## [1.0.10] - 2026-04-25

### Changed
- Split build.prop configuration; sorting and cleanup.

## [1.0.8] - 2026-04-25

### Added
- Bash parameter handling for selective patching.

## [1.0.7] - 2026-04-24

### Added
- Install patched Y1 music player APK.

## [1.0.6] - 2026-04-24

### Added
- Install patched `MtkBt.odex` for AVRCP 1.3 Java selector fix.

## [1.0.0] - 2026-04-23

### Added
- Initial release.
