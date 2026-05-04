# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version reflects `apply.bash`'s `# Version:` header; patch-script-only
changes are grouped under the bash version that was current at the time. For full
prose detail on any entry, see `git log` (commits are 1:1 with these bullets).

## [Unreleased]

### Added
- **`src/btlog-dump/`** — minimal no-libc ARM ELF that taps `mtkbt`'s undocumented `@btlog` abstract `SOCK_STREAM` socket (created by `socket_local_server("btlog", ABSTRACT, SOCK_STREAM)` at mtkbt vaddr `0x6b4d4`). Connecting to it as root yields a stream of `mtkbt`'s `__xlog_buf_printf` output (every `[AVRCP]` / `[AVCTP]` / `[L2CAP]` / `[ME]` / `SdpUuidCmp:` log line that is otherwise invisible to `logcat`) **plus** decoded HCI command/event traffic. Replaces both the conventional `persist.bt.virtualsniff` btsnoop knob (which breaks BT init on this device) and the `__xlog_buf_printf → logcat` redirect attempts (G1/G2 in `INVESTIGATION.md` — both crashed mtkbt). Same direct-syscall toolchain as `src/su/`, and the entry stub is reused (`Makefile` references `../su/start.S`). Build with `cd src/btlog-dump && make` → `src/btlog-dump/build/btlog-dump` (~1 KB, statically linked, no `NEEDED` entries). Build dir `.gitignore`d to match `src/su/build/`. Diagnostic-only — not invoked by any flag of the bash flow.
- **`tools/dual-capture.sh`** — pushes `src/btlog-dump/build/btlog-dump`, runs it under `su` simultaneously with `adb logcat -v threadtime` (specifying `-b main -b system -b radio` since Android 4.2.2 doesn't support `-b all`), and writes both streams + `dmesg` snapshots + `getprop` to a timestamped output dir. Per-line timestamps in both streams enable cross-stream correlation. Cleanup uses a `/proc/[0-9]*/comm` walk in pure shell to kill any lingering on-device `btlog-dump` (stock toybox on this device has no `pkill` / `killall`).
- **`tools/btlog-parse.py`** — Python decoder for the structured binary stream produced by `btlog-dump`. Walks `0x55 0x00 LEN ...` framing, extracts per-frame timestamp / sequence ID / severity / format-string-ID / text. `--tag-include` and `--tag-exclude` substring filters; `--from-ts` / `--to-ts` time-range filters; `--raw` to surface framing metadata. Note: timestamps come from multiple clock domains depending on severity (`0x12` = xlog text, `0xb4` = HCI snoop), so range filters use `continue` rather than early-`break`. Robust to leading-NUL sub-headers, cross-frame marker bleed, and variable-length format encodings; falls back to a "first run of ≥4 printable ASCII chars" heuristic for text extraction.
- **`tools/probe-postroot.sh`** + **`tools/probe-postroot-device.sh`** — one-shot post-root sanity probe. The host wrapper pushes the device-side script to `/data/local/tmp/probe.sh` and execs it under `su` (sidestepping `adb shell "su -c '<pipeline>'"` quoting issues). The device script enumerates: `mtkbt` PID via `/proc/[0-9]*/cmdline` walk (no `pidof` on stock toybox), `/proc/<pid>/maps` for PIE base + library load addresses, all MTK debug-node accessibility (`/proc/mtprintk`, `/proc/driver/wmt_aee`, etc.), canonical btsnoop file paths, BT-related `getprop` keys, `dmesg` AVRCP/AVCTP/STP traces, `/dev/stp*` permissions, `mtkbt` `strings` for `snoop`/`persist.bt`/`btsnoop` knobs, `libbluetooth*.so` strings for the same, `/proc/<pid>/status` capabilities, `gdbserver` presence anywhere, SELinux mode, ptrace policy, and `/proc/net/unix` for `bt.ext.adp.*` + `@btlog` abstract sockets. Pure shell builtins (no `awk` / `head` / `tail` on stock 4.2.2 toybox); custom `limit_first` / `limit_last` / `pid_of` via `read` / `case` / `set --`. Diagnostic-only.
- **`tools/release.sh`** — one-shot release helper. Bumps the bash's `# Version:` header, renames the `[Unreleased]` CHANGELOG section to `[<version>] - YYYY-MM-DD` (and prepends a fresh empty `[Unreleased]` above), commits both, and creates an annotated `v<version>` tag at HEAD. Optional `--push` flag pushes main + the new tag. Refuses to proceed if args aren't strict X.Y.Z semver, working tree has uncommitted changes, the tag already exists, or `[Unreleased]` has no bulleted entries. No partial-applies on failure.
- **`LICENSE`** — canonical GPLv3 text (FSF Version 3, 29 June 2007). The README has claimed GPLv3 since v1.0.8 but the file was missing.
- **`tools/install-android-sdk.sh`** — auto-installer for the Android SDK (Linux/macOS only). Detects existing `$ANDROID_HOME` and short-circuits; otherwise downloads Google's pinned commandline-tools archive, accepts licenses (`yes | sdkmanager --licenses`), installs `platforms;android-34` + `build-tools;34.0.0` + `platform-tools`, and writes `sdk.dir=…` into `src/Y1MediaBridge/local.properties` so Gradle finds the SDK without `ANDROID_HOME` in your shell. Bails clearly if JDK 17+ is missing. Disk ~1.5–2 GB, network ~1.7 GB. Idempotent: re-runnable, pin-bumpable via `CMDLINE_TOOLS_BUILD` at the top.

### Changed
- **`--artifacts-dir` is now optional; defaults to `./staging/` inside the repo, which now ships pre-created with a placeholder `staging/README.md`.** The common case is now `cp rom.zip staging/ && ./apply.bash --all` (no flag needed; no `mkdir` needed). Power users with artifacts on a different drive, multiple firmware versions, or a preference for keeping rom.zip outside the repo continue to pass `--artifacts-dir <path>` explicitly. `.gitignore` is `staging/*` + `!staging/README.md` — the placeholder tracks, but the staged `rom.zip` and any other contents stay out of commits. `git clean -dfx` will nuke whatever you stage there (intentionally — same fate as build artifacts), so keep a backup of `rom.zip` if you'd rather not re-download. README Quick start updated to use the new default; bash `--help` text updated to reflect the optional flag.
- **README polish for v2.0.0.** Overview's misleading "Bluetooth AVRCP 1.4" lead bullet (which described the now-known-broken `--avrcp` flow) reframed; the Artist→Album / generic "APK patching" bullets consolidated; an "AVRCP investigation tooling" bullet added that points at [Status](README.md#status). Redundant Scripts section dropped — its per-patcher detail already lives in [`src/patches/README.md`](src/patches/README.md) (table) and [`docs/PATCHES.md`](docs/PATCHES.md) (byte-level). Layout section gains `tools/` and `staging/` rows. "Manual patcher invocation" subsection collapsed to a one-liner pointer. "Verified against" trimmed (overlapped with the firmware manifest above it). 156 → 135 lines.
- Internal `mktemp` prefix in `apply.bash` switched from `y1-fixes.XXXXXX` to `koensayr.XXXXXX` (matches the rebrand).
- `src/Y1MediaBridge/README.md` — fix two stale `innioasis-y1-fixes.bash` link references (missed in the rename pass).
- `src/Y1MediaBridge/README.md` — clarify the bridge's status in the description: implementation is verified-correct, end-to-end metadata delivery is upstream-blocked by `mtkbt`. Cross-link to top-level Status / INVESTIGATION.
- `src/su/README.md` — drop the now-stale `--artifacts-dir <path>` from the deploy example (default is `staging/`).
- `tools/dual-capture.sh` — default output dir was hardcoded to `/work/logs/...` (developer-machine path); now `/tmp/koensayr-dual-<timestamp>/`. Header comment also said `-b all` while the code uses `-b main -b system -b radio`; comment fixed to match. Rationale text rephrased — was framed around the now-disproven `result:4096` lead.
- `tools/btlog-parse.py` — module docstring referenced a developer-machine capture path; now points at `INVESTIGATION.md` Trace #9 instead.
- `tools/install-android-sdk.sh` — error message said "AGP 8.x" but the project is on AGP 9.2.0; corrected.
- `apply.bash` — drop "(WIP)" from the `--avrcp` block's runtime `echo`s; phrasing now matches the warning printed at flag-parse time and the README's known-broken framing.
- `docs/ANDROID-SDK.md` — verify-install example dropped the no-longer-required `--artifacts-dir <dir>` from its `./apply.bash --avrcp` invocation.
- `apply.bash` `print_known_firmwares()`: the rom.zip / boot.img labels were swapped — rom.zip was tagged `(reference only, not consumed)` when it's actually the primary MD5-validated input, while boot.img (the field that *is* unconsumed since v1.7.0) had no annotation. Labels corrected and reordered to lead with the primary input.
- `src/patches/patch_mtkbt.py`: prepended a `Status (2026-05-04)` block summarising the now-conclusive "mtkbt is internally AVRCP 1.0; byte-patches can't make it process 1.3+ COMMANDs" finding and pointing at INVESTIGATION.md. The prior `Status (2026-05-03)` block (cardinality:0 gate located but unexplained) is preserved verbatim below for context, with a one-line resolution pointer.
- Spelling fix: three "disprov" non-word occurrences in CHANGELOG.md / INVESTIGATION.md → `disproof` / `disproven`.
- `src/patches/patch_mtkbt.py` docstring: corrected "Output md5: (regenerated on each build — see script output)" — the script actually has a fixed `OUTPUT_MD5 = "d47c904063e7d201f626cf2cc3ebd50b"` constant that it verifies against, so the docstring now states the expected MD5 directly.
- All `src/patches/patch_*.py` files (7 files) now have +x bit, matching their `#!/usr/bin/env python3` shebangs and consistent with `tools/btlog-parse.py`'s mode. The files were 100644 → 100755; doesn't change anything for the documented `python3 patch_xxx.py` invocation, but `./patch_xxx.py` now works too.
- `tools/dual-capture.sh` quick-decode hint at end-of-run: `grep -E "result.4096|CONNECT_CNF"` was suggesting `result.4096` as if it were still the lead — that was disproven 2026-05-04 (Trace #10 in INVESTIGATION.md: `0x1000` is just mtkbt's "request acknowledged" status code, set on every CNF). Updated to `grep -E "CONNECT_CNF|activeVersion|REGISTER_NOTIFICATION|tg_feature"` — useful filters for the current proxy-work direction.
- `src/patches/patch_y1_apk.py`: removed a hardcoded `/home/claude/.npm-global/lib/node_modules/apktool/bin/apktool.jar` dev-machine path that was being preferred over the in-tree `_patch_workdir/apktool.jar` if it happened to exist. The script now always uses the in-tree apktool (which it already auto-downloads on first run).
- `src/Y1MediaBridge/app/build.gradle`: switch all property-set syntax from the deprecated Groovy `propName value` form to the canonical `propName = value` form. AGP 9.2.0 / Gradle 9.5.0 emit deprecation warnings for the old form, and Gradle 10 will remove it. No functional change; lifts the future-Gradle blocker.
- `reference/` description in README's Layout section was misleading. The contained files are not stock baselines — `system/build.prop` has a literal `# Modified to fix ADB / Bluetooth` annotation and old-version patch entries (`persist.bluetooth.avrcpversion=avrcp13`); `audio.conf` has the `Enable=Source,Control,Target` / `Master=true` values that `apply.bash` writes; `auto_pairing.conf` has the empty blacklists that `apply.bash` empties. Description corrected to "example post-patch state… not consumed by the build, not stock baselines".
- Sub-README `## See also` parity: `src/btlog-dump/README.md` was missing the top-level `README.md` link that the other three sub-READMEs include; `src/patches/README.md`'s CHANGELOG entry said "per-patcher version history" while the other three say "top-level changelog". Both normalised.
- `apply.bash` `--help` text and its `--avrcp` warning referenced `INVESTIGATION.md` "2026-05-04 conclusion" in one place and `"Conclusion (2026-05-04)"` in another. Both now match the actual heading (`# Conclusion (2026-05-04) — byte-patch path exhausted, proxy work needed`).
- CHANGELOG bullet referenced `INVESTIGATION.md "Conclusion (2026-05-04) — path forward"` — that string isn't a real heading. INVESTIGATION has separate `# Conclusion (2026-05-04)` and `## Path forward` sections. Reference corrected.
- README flag-table and `apply.bash --help` for `--adb` claimed it sets `debuggable` — that's not a real Android property name (it would be ambiguous between `ro.debuggable` and `persist.service.debuggable`). The bash actually writes `persist.service.adb.enable=1` + `persist.service.debuggable=1`. Both docs corrected to use the full property names.
- `apply.bash` error-message normalization: two error paths used sentence-case `Error: …` and wrote to stdout, while the other ten error paths use `ERROR: …` to stderr. Both outliers fixed.
- `apply.bash` `--avrcp` warning block: 8 of the 9 lines were going to stdout, only the final line was redirected to stderr. Normalised — the entire warning + the trailing blank line now go to stderr (matches the surrounding error-handling convention).
- `apply.bash` Unknown-option path: blank line and `show_help` invocation were going to stdout while the surrounding `ERROR: …` line went to stderr. Both moved to stderr for stream consistency (so `./apply.bash --bad 2>/dev/null` produces no output).
- `tools/release.sh`: usage examples used `1.11.0` as a placeholder — that's the version that *would* have come next under the old numbering scheme but was superseded by the v2.0.0 jump. Updated to `2.0.0` so the example matches the next actual release.
- `apply.bash` defensive sudo handling: previously called `sudo -v` without checking whether `sudo` even exists in PATH or whether the auth prompt succeeded. On a system without sudo (or where the user cancelled the prompt), the script printed `sudo: command not found` to stderr but continued running, falling through to `mount`/`chown` invocations that also failed mid-flight. Now checks for sudo upfront and bails with `ERROR: 'sudo' is required …` if missing; checks `sudo -v`'s exit code and bails with `ERROR: sudo authentication failed` on cancel/failure.
- **Critical fix.** `src/patches/patch_y1_apk.py` referenced `_NPM_APKTOOL` at line 226 (in the "Locate or download apktool" step) but the constant itself was removed earlier in this same release cycle (the `/home/claude/...npm-global` dev-machine path leak fix). Running the patcher would `NameError: name '_NPM_APKTOOL' is not defined` at the first apktool-locate step — which means **every actual `--music-apk` invocation would crash**. Removed the dead branch entirely; the remaining download-or-reuse logic always uses the in-tree `_patch_workdir/apktool.jar`. Caught by audit running `importlib.spec_from_file_location` against every patcher.
- `tools/release.sh`: comment said "last chance to abort" but no abort mechanism existed — the script printed a summary then immediately proceeded to commit + tag. Added a `Proceeding in 3 seconds (Ctrl-C to abort)...` pause + `sleep 3` after the summary, matching the comment's promise. Non-interactive use (CI, etc.) just experiences a 3-second delay.
- **`--help` handling added to seven scripts that previously did the wrong thing.** Discovered by probing every executable in `tools/` + `src/patches/` with `--help`. Three scripts had **destructive** behaviour (running --help triggered actual side effects): `tools/setup.sh --help` was running the full setup (cloning MTKClient ~97 MB + creating venvs); `tools/install-android-sdk.sh --help` was starting a ~165 MB SDK download; `tools/probe-postroot-device.sh --help` was running the full probe sequence. Four scripts had unhelpful failure paths: `tools/dual-capture.sh --help` and `tools/probe-postroot.sh --help` failed at the "no device" check; `tools/release.sh --help` failed with "ERROR: '--help' is not a strict X.Y.Z semver"; `src/patches/patch_y1_apk.py --help` exited with `ERROR: '--help' not found.` (treated --help as the APK path). All seven now check for `-h|--help` upfront and print actual usage info; the destructive scripts now do nothing on --help.
- **Critical fix.** `apply.bash` infinite-loops when any value-taking flag (`--artifacts-dir`, `--mtkclient-dir`, `--python-venv`) is passed as the last arg with no value. Root cause: `shift 2` is a no-op when `$#` < 2 (just emits "shift count out of range" silently), so the parser keeps re-matching the same flag forever, pegging CPU at 100%. Discovered while testing edge cases — `bash apply.bash --root --artifacts-dir` ran for 2+ minutes before I killed it. Added a `require_value <flag-name> <value>` helper that bails with `ERROR: <flag> requires a value` if the value is missing or starts with `--` (catching `--artifacts-dir --root` too). Same fix applied to all three value-taking flags.
- `apply.bash` mount-handling defenses: previously did `sudo mount -o loop "$dst" "${PATH_MOUNT}/"` without (1) creating `/mnt/y1-devel` if missing, (2) checking that nothing was already mounted there, or (3) checking the mount's exit code. If `/mnt/y1-devel` didn't exist → mount failed, script proceeded to copy/chown calls that all failed. If something was already mounted there (e.g. previous apply.bash interrupted by Ctrl-C while mounted), mount silently failed and patches were applied to the *prior* image. Now: pre-creates the dir if missing, errors out cleanly if mountpoint is busy with instructions to umount, checks the mount call's exit code.
- `apply.bash` cleanup trap: `_cleanup` removed `PATH_TMP_STAGE` and killed the sudo keepalive but did **not** unmount `/mnt/y1-devel` if the mount had succeeded earlier. A failed patcher mid-flight would leave the system.img mounted, requiring manual `sudo umount /mnt/y1-devel` before re-running. Now tracks mount state (`MOUNTED=true/false`) and the cleanup trap unmounts on EXIT.
- **Critical fix.** `--remove-apps` has been a **complete no-op** for the entire project's history. The `apps_to_remove` array contains shell glob patterns (`ApplicationGuide.*`, `Calendar*`, etc.) but the rm invocation was `sudo rm -rf "${PATH_MOUNT}/app/${app}"` — the double quotes around the entire path **suppress glob expansion**, so `rm -rf` got literal strings like `/mnt/y1-devel/app/ApplicationGuide.*` (with a literal `*` character). Since no file with that exact name exists, `rm -rf -f`-equivalent behavior is silent no-op. Verified empirically by running the same pattern against a test directory: zero files removed. Switched to `find "${PATH_MOUNT}/app" -maxdepth 1 -name "${app}" -exec rm -rf {} +` — `find -name` does its own glob matching independent of shell quoting, and the `-maxdepth 1` keeps the scope bounded. Verified the find form correctly removes both flat APKs (`Foo.apk`, `Foo.odex`) and subdirectories (`Calendar/`, `CalendarProvider/`).
- README Requirements section overstated cross-platform support. The bash uses `mount -o loop` (Linux-only) and GNU-style `sed -i 'pattern' file` (BSD/macOS sed would need `-i ''`), so the patcher path can't actually run on macOS as previously implied by the `md5sum (Linux) or md5 -q (macOS)` line. Updated to clarify "Linux host" upfront; cross-platform MD5 detection is preserved for the rom.zip validation step but the patcher path itself is Linux-only.
- `.gitignore`: added `tools/.cmdline-tools-*.zip` to catch the partial-download state. `tools/install-android-sdk.sh` downloads `tools/.cmdline-tools-linux-XXXXXXX.zip` (~165 MB) and `rm`'s it on success — but on failure mid-flight (network drop, sigkill, etc.) the file lingered as untracked-but-not-ignored, where `git add tools/` would happily stage it. Now ignored at the pattern level so partial state can't accidentally land in commits.
- `apply.bash` defensive pre-check: previously checked for `sudo`, `md5sum`, `unzip`, and `simg2img` (conditional) but NOT `python3` — even though every byte patcher and the MTKClient flash step shells out to `python3`. On a system without python3 (rare on Linux but possible on minimal containers), the script would have failed mid-flight with `python3: command not found` on the first patcher invocation. Added an upfront `command -v python3` check that bails with a clear ERROR pointing at `tools/setup.sh`.
- `tools/release.sh` defensive pre-check: previously validated semver / clean tree / no existing tag / non-empty [Unreleased] but didn't check that `git config user.name` and `user.email` were set. On a system without those configured, the script would fail at `git commit -m "$COMMIT_MSG"` after already mutating apply.bash's `# Version:` and CHANGELOG.md (an inconsistent half-applied state). Added an upfront check next to the clean-tree validation that bails with `ERROR: git user.name and/or user.email not set` and shows the `git config --global` commands to set them.
- `apply.bash` `simg2img` invocation now checks exit code. Previously `simg2img "$PATH_SYSTEM_IMG" "$raw"` ran unguarded; on failure (corrupt sparse image, disk full mid-conversion, etc.) the script silently continued with `PATH_SYSTEM_IMG="$raw"` pointing at a missing or partial file, then failed confusingly at the subsequent md5sum step. Now bails with `ERROR: simg2img conversion failed (corrupt sparse image, or disk full?)`.
- `apply.bash` `cp "$PATH_SYSTEM_IMG" "$dst"` now checks exit code. Previously this copy of the validated raw system.img into the artifacts dir wasn't guarded; on failure (read-only artifacts dir, full disk, etc.) the script silently continued and tried to `mount -o loop` a non-existent or stale file. Now bails with `ERROR: failed to copy system.img to ${dst} (disk full? read-only artifacts dir?)`.
- **`apply.bash` `sudo umount` exit code now checked.** Previously the post-patch unmount step ran unguarded; if umount failed (busy mount — open file inside `/mnt/y1-devel`, e.g. a stray editor), the script set `MOUNTED=false` and proceeded to flash a still-mounted image — the kernel's loop-back driver would have dirty pages racing against `mtkclient`'s direct device write. Now bails with `ERROR: umount ${PATH_MOUNT} failed (busy mount? open file in there?). Refusing to flash a still-mounted image — kernel may have dirty pages.`
- **`apply.bash` `python3 mtk.py w android …` exit code now checked.** Previously the actual flash step ran unchecked; on failure (device not in BROM mode, USB cable not data-capable, libusb missing, etc.) the script printed `Deactivating MTKClient venv..` then `Done!` and exited 0 — the user would think the flash succeeded. Now bails with a clear ERROR listing the common causes, deactivates the venv, and exits 1.
- **Project is now unambiguously Linux-only.** Since the patcher path can't run on macOS regardless, building Y1MediaBridge.apk on macOS produces a file with no consumer. Stripped all remaining macOS hints to remove ambiguity:
  - `apply.bash` md5_of: dropped the `md5 -q` macOS fallback (dead code — the patcher fails earlier on mount)
  - `apply.bash` simg2img-missing hint: dropped the `brew install simg2img (macOS)` line
  - `tools/install-android-sdk.sh`: OS-detect case now bails on anything but Linux with a clear "patcher uses Linux-only commands; run from a Linux host or VM" message; the Darwin → mac mapping was removed; `--help` text now says "Linux" not "Linux/macOS"; Java install hint dropped the macOS Homebrew line
  - `docs/ANDROID-SDK.md`: rewritten as Linux-only; removed the entire `## macOS` section (Homebrew + manual download), the `## Windows` section, and the multi-platform install-path table. Manual fallback section trimmed to Linux distros only
  - README Requirements `--avrcp` line: dropped "on Linux/macOS" qualifier from the SDK-installer description
  - README Documentation section: dropped "Linux / macOS / Windows" qualifier from the docs/ANDROID-SDK.md pointer
  
  Remaining macOS mentions are all of the form "macOS isn't supported, here's the workaround (Linux VM)" — useful for macOS users hitting the project, never claims of compatibility.
- **Project rebrand: "Innioasis Y1 Firmware Fixes" → "Koensayr".** Hybrid pattern — the project's brand name is `Koensayr` (a Star Wars deep cut: Koensayr Manufacturing made the Y-Wing starfighter, Y-Wing → Y1), while the GitHub repo name remains a discoverability-friendly descriptive identifier (`y1-mods`) so Y1 owners searching for firmware tooling can find the project. README headline + tagline updated; `apply.bash`'s header self-description updated to "Koensayr (Innioasis Y1 system.img patcher)". Body references to the device "Innioasis Y1" stay — that's still the device's name. `INVESTIGATION.md` is unchanged. The rename has no effect on user-facing flags or behaviour.
- **Renamed the orchestration script `innioasis-y1-fixes.bash` → `apply.bash`.** Reads more naturally ("apply the fixes"), avoids the `cd y1-fixes && ./y1-fixes.bash` repetition, and decouples the script name from the company name in anticipation of a future repo-name change. All in-repo references updated (README, INVESTIGATION.md, sub-script READMEs, the bash's own `--help` output, `tools/release.sh`, etc.). External users / scripts that hardcoded `./innioasis-y1-fixes.bash` will need to update; no compatibility shim is provided (this is a v2.0.0 release).
- **`--avrcp` is now a known-broken opt-in.** Empirical testing across five distinct (version, features) byte-patch combinations against Sonos Roam (a known-working AVRCP CT validated against Pixel 4 at every AVRCP version 1.3-1.6) confirms the byte-patch path cannot deliver AVRCP 1.4 metadata. mtkbt is internally an AVRCP 1.0 implementation (compile-time `[AVRCP] AVRCP V10 compiled`, runtime `AVRCP register activeVersion:10`); byte-patches successfully shape the on-wire SDP record but cannot make the daemon process AVRCP 1.3+ COMMANDs that peers send in response. The patches additionally regress stock AVRCP 1.0 PASSTHROUGH (play/pause from car/headset stops working). `--avrcp` continues to run if explicitly specified (useful for the user-space proxy work — see `INVESTIGATION.md` "Conclusion (2026-05-04)") and prints a startup warning. **Excluded from `--all`'s expansion.** See `INVESTIGATION.md` for the full negative-result write-up.
- **`--bluetooth` no longer sets `persist.bluetooth.avrcpversion=avrcp14`.** Setting that property committed the device to an AVRCP version mtkbt couldn't deliver. The remaining audio.conf / `auto_pairing.conf` / `blacklist.conf` / `ro.bluetooth.class=2098204` / `ro.bluetooth.profiles.a2dp.source.enabled=true` / `ro.bluetooth.profiles.avrcp.target.enabled=true` properties are all pairing-essential and stay. `--bluetooth` continues to be required for car/peer pairing to work correctly.
- **`--all` now expands to `--adb` + `--bluetooth` + `--music-apk` + `--remove-apps` + `--root`.** `--avrcp` is intentionally excluded.
- README rewritten throughout to reflect the conclusive negative on the byte-patch path: `Status` section condensed to a one-paragraph operational summary plus a pointer at `INVESTIGATION.md`; `Quick start` simplified (Android SDK install + Y1MediaBridge gradle build are now described as conditional pre-reqs only when opting in to the broken `--avrcp`); `Diagnostics` section collapsed to one line per tool with pointers at the sub-READMEs; flag table trimmed (removed `<nobr>` tags, trimmed `--avrcp` and `--bluetooth` Effect cells to one-liners); new top-level `Diagnostics` section documenting the `@btlog` tap workflow and the post-root probe; `Layout` and `Scripts` sections cross-link the new `src/btlog-dump/` tree.
- `INVESTIGATION.md` extended with a "Conclusion (2026-05-04) — byte-patch path exhausted, proxy work needed" section. Includes the test matrix, the `mtkbt is AVRCP 1.0 internally` finding, the disproof of the `result:4096` lead, the current repo state after the cleanup, and a four-phase sketch of the user-space proxy work for the next agent: gdbserver-identify drop site → trampoline-forward to JNI → Java AVRCP COMMAND parser/responder → outbound RSP path. Documents the verification target (cardinality > 0, Sonos shows metadata, PASSTHROUGH still works) and the known prerequisites. Also folded in the granular reference detail (byte-level patch tables, MD5s, function offsets, ILM layouts, msg_id maps, log tag conventions, post-root traces #8–#11) that previously lived only in the working-notes brief.
- Standardise prose spelling to **`MTKClient`** (no space, capitalised) across README/INVESTIGATION/CHANGELOG. Code identifiers (`tools/mtkclient/` paths, `--mtkclient-dir` flag, `MTKCLIENT_DIR` env var, `OVERRIDE_MTKCLIENT_DIR` / `resolve_mtkclient_dir` bash symbols) are intentionally kept lowercase.
- **Bump AGP 8.7.3 → 9.2.0** in `src/Y1MediaBridge/build.gradle`. AGP 9.2.0 is the latest stable per Google's maven repository; 8.7.3 was from late 2024 and ~5 minor versions behind. AGP 9.x removes some long-deprecated DSL methods. Modernized `app/build.gradle` to match: `compileSdkVersion 34` → `compileSdk 34`, `minSdkVersion 17` → `minSdk 17`, `targetSdkVersion 17` → `targetSdk 17`. The `*Version` forms have been deprecated since AGP 7.x; the new property forms have been the canonical syntax for years and work on AGP 8.x too.
- **Bump cmdline-tools build 11076708 → 14742923** in `tools/install-android-sdk.sh`. The 11076708 pin was from August 2023; 14742923 is the latest published by Google. Verified that both the linux and mac variants of the new build are reachable at `dl.google.com/android/repository/`.
- Bake `--stop` into the documented `./gradlew assembleDebug` invocations (top-level README Quick start, `src/Y1MediaBridge/README.md` Build section). Gradle's build daemon caches the JVM it started with, so a `JAVA_HOME` change between builds doesn't take effect until the daemon restarts — and the resulting `[JAVA_COMPILER]` failure looks identical to a real toolchain problem. `./gradlew --stop && ./gradlew assembleDebug` always-stops the daemon first; cheap and prevents the most common newcomer-tripping error.
- Walk back the "JDK 22+ likely to fail" warning added in `c794239`. Empirical test on Fedora 43 with JDK 25 + AGP 8.7.3 + Gradle 9.5.0 builds cleanly. The original failure that motivated the warning turned out to be a stale gradle daemon (started with a JRE-only JDK 25, kept alive across `JAVA_HOME` changes) — not an AGP-vs-JDK incompatibility. `tools/install-android-sdk.sh` no longer warns on JDK > 21; only the JDK ≥ 17 floor stays. `docs/ANDROID-SDK.md` and `src/Y1MediaBridge/README.md` updated accordingly: confirmed working with JDK 17, 21, 25.
- `docs/ANDROID-SDK.md` adds a "Gotcha — gradle daemon caching" subsection: gradle keeps its build daemon alive across invocations, so changing `JAVA_HOME` after a build has run leaves the cached daemon on the old JVM (and reproduces the same `[JAVA_COMPILER]` error). Run `./gradlew --stop` before rebuilding when changing JDKs. Includes pointer to `./gradlew --version` for inspecting the daemon JVM path.
- Make the `source tools/android-sdk-env.sh` step impossible to miss after `install-android-sdk.sh` finishes. The trailing summary now ends with a boxed `NEXT STEP` callout containing the exact `source …` line; the gradle-build invocation that *doesn't* need ANDROID_HOME is mentioned separately so users don't conflate the two. `tools/setup.sh`'s post-completion message tightened to a two-line "do these in order" hint.
- `tools/setup.sh` post-completion message points at `install-android-sdk.sh` for users who'll use `--avrcp`.
- `docs/ANDROID-SDK.md` rewritten to lead with the auto-installer; the per-platform manual recipes remain as the fallback (for Windows, supply-chain-restricted environments, or users who'd rather configure a system-wide install).

### Removed
- **Failed in-tree experiment scripts** (all four — Browsing-bit, Pixel-shape, Pixel-1.3 mimic, features-only). All confirmed disproven; the scripts were testing-only by design. Their results are summarised in `INVESTIGATION.md` "Conclusion (2026-05-04)" and in this CHANGELOG. Removed: `tools/experiment-pixel-shape.sh`, `tools/patch_mtkbt_pixel_shape.py`, `tools/experiment-features-01.sh`, `tools/patch_mtkbt_features_01.py`, `tools/experiment-pixel-1-3.sh`, `tools/patch_mtkbt_pixel_1_3.py`, `tools/patch_mtkbt_odex_pixel_1_3.py`. Diagnostic tooling (`@btlog` tap + dual-capture + parser + post-root probe) is unaffected and remains in-tree for the proxy-work prep.

### Fixed
- `src/patches/patch_y1_apk.py`: silence androguard 4.x's loguru output. The existing `logging.getLogger("androguard").setLevel(logging.ERROR)` line only suppresses the stdlib `logging` channel; androguard 4.0 switched to [loguru](https://github.com/Delgan/loguru) for its own logging, which ignores stdlib config. Result: a flood of `androguard | INFO`-style lines on every APK parse. Added a try/except `from loguru import logger; logger.disable("androguard")` block at module load (preventive — only if loguru is available transitively) and again immediately before the `from androguard.core.apk import APK` inside `get_apk_info` (just-in-time — covers the case where the module-load attempt hit ImportError because androguard hadn't been pulled in yet). Both stdlib + loguru channels now silenced.
- `tools/install-android-sdk.sh`: license-accept step was failing silently — two compounding bugs. (1) `>/dev/null` on the original `yes | sdkmanager --licenses >/dev/null` swallowed any sdkmanager error, so the failure produced no diagnostic. (2) Even after removing the redirect, the line still failed because `yes | sdkmanager` gets SIGPIPE'd: when sdkmanager exits cleanly after accepting licenses, the still-running `yes` gets SIGPIPE and exits with status 141; `set -o pipefail` then propagates that 141 as the pipe's exit status even though sdkmanager itself succeeded ("All SDK package licenses accepted."). Switched to process substitution: `sdkmanager --licenses < <(yes)`. Process substitution runs `yes` in a background subshell whose exit isn't part of the foreground command's accounting, so SIGPIPE on `yes` no longer affects the script's exit. Both `sdkmanager` invocations (`--licenses` and `--install`) also now wrapped in explicit `if !` blocks that print a useful error including the manual-debug invocation and current `java -version`.
- `tools/install-android-sdk.sh`: download/unpack step wasn't fully idempotent — partial state from a prior failed run could survive across re-runs and break `mv` / `unzip`. Restructured: skip download/unpack entirely if `cmdline-tools/latest/bin/sdkmanager` already exists; otherwise wipe any half-extracted `cmdline-tools/` first and use `unzip -o` to overwrite.
- `tools/install-android-sdk.sh`: previously short-circuited the **entire** script (including the `local.properties` write) when `tools/android-sdk/` was already populated from a prior run. Net effect: a re-run or recovery from a partial first run never wired Gradle to the SDK, so `./gradlew assembleDebug` kept failing with `SDK location not found`. Restructured: download/install is the only step gated by the existing-SDK check; `local.properties` and the new env-file write **always** run, healing missing config.
- `tools/install-android-sdk.sh`: also generate `tools/android-sdk-env.sh` — a sourceable file that exports `ANDROID_HOME` and adds `cmdline-tools/latest/bin` + `platform-tools` to `PATH`. Lets users run `adb` / `sdkmanager` from their shell with `source tools/android-sdk-env.sh` (Gradle itself doesn't need this — it reads `local.properties`). Fixes the user-reported "script doesn't export ANDROID_HOME" gap. `.gitignore` updated to exclude the env file (per-machine, contains absolute paths).
- `tools/setup.sh`: drop `--quiet` from the four `pip install` invocations. With it set, pip suppressed all output during venv provisioning; the script appeared to hang for 1–3 minutes (MTKClient's `requirements.txt` pulls a non-trivial set of native deps). User-reported as "freezes". Pip's default progress is informative enough; if needed back, prefer `--progress-bar off` over `--quiet`.
- `tools/setup.sh`: pinned MTKClient ref was `2.1.4.1`; the actual upstream tag is `v2.1.4.1` (with the `v` prefix). Fresh runs were failing at `git checkout 2.1.4.1` with `pathspec did not match any file(s) known to git`. Pin updated.
- `tools/setup.sh`: same partial-state bug as `install-android-sdk.sh` — if `git clone` succeeded but `git checkout` failed (e.g. on the bad ref above), `tools/mtkclient/` existed on disk so a re-run silently skipped the clone *and* the checkout, leaving MTKClient at HEAD-of-main. Restructured: clone is gated by directory presence, but the checkout always runs (idempotent — `git checkout` to current ref is a no-op). If the ref isn't in the local clone, `git fetch --tags` first; if it still doesn't resolve, bail with `git ls-remote` instructions. Re-running heals.
- Self-referential "the brief" mentions in `INVESTIGATION.md` and `tools/probe-postroot.sh`/`tools/dual-capture.sh` cleaned up after the brief content was folded into `INVESTIGATION.md` as an appendix.

### Documentation
- README Quick start now includes `./tools/install-android-sdk.sh` as a sibling step to `./tools/setup.sh`. Requirements bullet for `--avrcp` updated.
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
- README Requirements: dropped the "MTKClient 2.1.4.1 at /opt/..." line (now covered by `tools/setup.sh`); added a line about the Android SDK being needed only for `--avrcp`.
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
- Drop the in-file version-history block from `apply.bash` (~30 lines); `git log` and `CHANGELOG.md` are authoritative.
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
- Boot.img extraction, `patch_bootimg` invocation, and boot.img MTKClient flash dropped from the bash. `patch_adbd.py` and `patch_bootimg.py` kept in the tree as historical record.

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
