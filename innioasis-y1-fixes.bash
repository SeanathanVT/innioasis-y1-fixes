#!/usr/bin/env bash
#
# Script: innioasis-y1-fixes.bash
# Description: Patches Innioasis Y1 system.img to fix Bluetooth AVRCP, remove APK-related cruft, and enable ADB debugging.
# Author: Sean Halpin (github.com/SeanathanVT)
# Version: 1.8.0
# History:
# 2026-05-03 (1.8.0): Reintroduce --root flag with a fundamentally different mechanism — install a minimal setuid-root `su` binary at /system/xbin/su (mode 06755, root:root) instead of patching /sbin/adbd in the ramdisk. The H1/H2/H3 adbd byte patches (v1.3.x–v1.6.0) all caused "device offline" on hardware because something in the OEM adbd's startup sequence depends on the syscalls actually changing the uid (we couldn't see what without on-device visibility, which we lost the moment we shipped a broken adbd). The new approach leaves /sbin/adbd untouched: stock adbd starts at uid 2000 (shell) as normal, ADB protocol comes up cleanly, and root is obtained by running /system/xbin/su from the adb shell. The su binary is a ~900-byte direct-syscall ARM-EABI ELF compiled from su/ in this repo (no libc, no manager APK, no whitelist) — every byte traces to GCC + the local source. See su/su.c and su/start.S; the bash references the prebuilt artifact at ${PATH_SCRIPT_DIR}/su/build/su (run `cd su && make` to build). The --root flag is a system.img-only operation now (no boot.img extraction, no ramdisk repack); it copies the prebuilt binary into the mount and chmods 06755. Re-added to --all. patch_adbd.py and patch_bootimg.py remain in the tree as historical record (still unwired).
# 2026-05-03 (1.7.0): Remove --root flag entirely. The H1/H2/H3 byte patches in /sbin/adbd (both the NOP-the-blx and arg-zero revisions) caused "device offline" on hardware — adbd starts and the USB endpoint enumerates, but the ADB protocol handshake never completes. Without on-device visibility (logcat / dmesg / strace, all of which require ADB), we can't diagnose what about adbd-at-uid-0 breaks the protocol on this OEM build. The standalone patch_adbd.py and patch_bootimg.py scripts are kept in the tree as historical record (with warning notes in their docstrings); their analysis of the drop_privileges block, bionic syscall wrappers, and cgroup-migration helper is preserved for whoever picks the root pass back up. Re-introducing --root is straightforward (re-add the boot.img extraction + patch_bootimg invocation + boot.img flash) once a working approach is found.
# 2026-05-03 (1.6.0): Take the official OTA `rom.zip` as the primary firmware input. The bash now MD5-validates rom.zip against the KNOWN_FIRMWARES manifest, then `unzip -j -o` extracts only the files needed by the active flags (system.img for system-affecting flags, boot.img for --root) into the tempdir. Each extracted file's MD5 is cross-verified against the manifest as a defensive check (rom.zip MD5 is collision-resistant so this is essentially redundant, but cheap). Replaces the v1.5.0 flow that took separately-staged boot.img and system.img — users now stage just rom.zip + Y1MediaBridge.apk. The sparse-detect / simg2img path still applies to the extracted system.img since future firmware versions might bundle a sparse one. `unzip` is now a hard dependency.
# 2026-05-03 (1.5.0): Replace hardcoded VERSION_FIRMWARE="3.0.2" with stock-firmware MD5 validation. A KNOWN_FIRMWARES manifest holds (version, system.img md5, boot.img md5, rom.zip md5, music-APK filename) tuples; staged inputs are MD5-validated against it post-staging (i.e. after any simg2img conversion, since the canonical comparison is against the raw image). VERSION_FIRMWARE is derived from the lookup. If both system.img and boot.img are processed, both must resolve to the same version. On unknown input the script bails and prints the manifest. Currently only v3.0.2 is enrolled. Cross-platform MD5: prefers `md5sum` (Linux), falls back to `md5 -q` (macOS).
# 2026-05-03 (1.4.1): Auto-handle sparse system.img — detect via `file` (or sparse magic 0xed26ff3a) and run simg2img automatically into the working copy. Previously the user had to manually convert sparse → raw before staging, since `mount -o loop` rejects sparse format. simg2img is required when the input is sparse; if it's missing the script bails with install instructions for Debian/Ubuntu, Arch, and macOS.
# 2026-05-03 (1.4.0): Drop the pre-staged-artifacts requirement. --avrcp and --music-apk now extract the stock binaries directly from the mounted system.img, run the corresponding patch_*.py against them, and write the patched bytes back in-place. Previously the user had to run each patch_*.py manually beforehand and stage mtkbt.patched / MtkBt.odex.patched / libextavrcp.so.patched / libextavrcp_jni.so.patched / com.innioasis.y1_3.0.2-patched.apk in --artifacts-dir. Only Y1MediaBridge.apk (externally-built, not derived from system.img) and boot.img (for --root) need to be staged now; everything else is extracted from system.img and patched on the fly. New helpers `patch_in_place_bytes` and `patch_in_place_y1_apk` wrap the extract → patch → write-back cycle. Idempotent: re-running detects already-patched files and is a no-op.
# 2026-05-03 (1.3.2): No functional changes to the bash itself — reflects patch_bootimg.py absorbing patch_adbd.py (H1/H2/H3 NOP the three blx setgroups/setgid/setuid calls in adbd's drop_privileges block). With the adbd binary patched, `adb shell` returns uid 0 directly at boot, and `adb root` is no longer needed (it returns "already running as root" without triggering the USB-rebind cycle). --root help text updated accordingly — the v1.3.1 "do not run adb root" warning was correct only against the v1.3.1 patcher (which relied on inert default.prop edits).
# 2026-05-03 (1.3.1): --root no longer touches system.img (skip copy/mount/patch/unmount/flash and the sudo prompt unless a system-affecting flag is set). --root help text initially warned against running `adb root` post-flash (the OEM adbd ignores ro.secure, so v1.3.1's default.prop edits left adbd at uid 2000 and `adb root` triggered a USB-rebind cycle that lost the host connection). Superseded by v1.3.2 which patches the adbd binary directly.
# 2026-05-03 (1.3.0): Reintroduce --root flag. Delegates to patch_bootimg.py (pure-Python in-place cpio mutation; no shell-side cpio/dd repack). Flashes patched boot.img via mtkclient after system.img write.
# 2026-04-30 (1.2.2): No functional changes — reflects patch_mtkbt.py update to include AVCTP 1.0→1.3 patches (B1-B3).
# 2026-04-26 (1.2.1): Add libextavrcp.so.patched deployment to --avrcp.
# 2026-04-26 (1.2.0): Remove --root flag and boot.img handling (broken).
# 2026-04-26 (1.1.3): Prompt for sudo credentials upfront to prevent mid-execution prompt.
# 2026-04-26 (1.1.2): Fix --root: sudo cpio to preserve device nodes; add ro.adb.secure=0 and service.adb.root=1. Remove fail on size mismatch (non-issue).
# 2026-04-26 (1.1.1): Fix macOS compatibility: replace stat -c%s with wc -c for file size.
# 2026-04-26 (1.1.0): Add --root flag to patch boot.img ramdisk for ADB root access.
# 2026-04-26 (1.0.11): Update --avrcp to deploy AVRCP 1.4 patched binaries (.patched filenames).
# 2026-04-25 (1.0.10): Split build.prop configuration stuff. More sorting. More cleanup. More renames.
# 2026-04-25 (1.0.9): Sort some stuff to make it look cleaner (yes I still care about this).
# 2026-04-25 (1.0.8): Add bash parameter handling for selective patching.
# 2026-04-24 (1.0.7): Install patched Y1 music player APK.
# 2026-04-24 (1.0.6): Install patched MtkBt.odex for AVRCP 1.3 Java selector fix.
# 2026-04-23 (1.0.5): Fine tune echo statements.
# 2026-04-23 (1.0.4): Use unmodified (non-sparse) system.img source.
# 2026-04-23 (1.0.3): Add explicit Python virtual environment activation / deactivation.
# 2026-04-23 (1.0.2): Convert app removal to loop because it looks prettier.
# 2026-04-23 (1.0.1): Append to build.prop, do not overwrite (oops).
# 2026-04-23 (1.0.0): Initial release.
# Usage: ./innioasis-y1-fixes.bash [OPTIONS]
#

show_help() {
  cat <<EOF
Usage: ./innioasis-y1-fixes.bash --artifacts-dir <path> [OPTIONS]

--artifacts-dir <path> is mandatory and specifies the directory containing the
firmware images and any externally-built artifacts.

Required artifacts (depending on flags):
  rom.zip                — mandatory if ANY patch flag is set. The official
                            Innioasis Y1 OTA package. MD5 is validated against
                            the KNOWN_FIRMWARES manifest in this script; the
                            matched firmware version drives all version-
                            dependent filenames. The bash extracts system.img
                            from the zip into a tempdir, cross-verifies its
                            MD5 against the manifest as a defensive check,
                            auto-de-sparses via simg2img if needed, mounts
                            it as a loop device, and the four BT binaries +
                            music APK are extracted, patched in-place by the
                            corresponding patch_*.py, and written back. No
                            pre-staged .patched files are required.
  Y1MediaBridge.apk      — mandatory if --avrcp is set. This is an externally-
                            built artifact (not derived from the OTA), so it
                            must be staged separately and is not MD5-validated.

If only --artifacts-dir is specified, this help message is displayed.
If any patching option is specified, the script will mount the system.img, apply the selected
patches (auto-extract → patch → write-back where applicable), and then write the patched
system.img to the device.

MANDATORY:
  --artifacts-dir <path> Directory containing system.img / boot.img /
                          Y1MediaBridge.apk as listed above

OPTIONS:
  --adb                Enable ADB debugging
  --avrcp              Enable AVRCP 1.4 support (WIP - pending flash verification)
  --bluetooth          Configure Bluetooth fixes
  --music-apk          Patch Y1 music player APK (Artist→Album navigation)
  --remove-apps        Remove unnecessary APK files from system
  --root               Install /system/xbin/su (setuid-root escalator).
                        Requires a prebuilt binary at su/build/su — run
                        \`cd su && make\` once before using this flag.
                        Stock /sbin/adbd is untouched; root is obtained
                        post-flash by running \`adb shell /system/xbin/su\`.
  --all                Apply all patches (equivalent to all flags above)
  -h, --help           Display this help message

NOTE: --root in v1.8.0 installs a setuid-root su binary, replacing the
v1.3.0–v1.6.0 approach of patching /sbin/adbd in the boot.img ramdisk.
The adbd byte patches (H1/H2/H3, both NOP-the-blx and arg-zero revisions)
caused "device offline" on hardware. The standalone patch_adbd.py and
patch_bootimg.py scripts are kept in the tree as historical record — see
their docstrings for the analysis.

EXAMPLES:
  # Apply all patches
  ./innioasis-y1-fixes.bash --artifacts-dir /path/to/artifacts --all

  # Apply only specific patches
  ./innioasis-y1-fixes.bash --artifacts-dir /path/to/artifacts --bluetooth --music-apk --remove-apps

EOF
}

# Initialize flags
FLAG_ADB=false
FLAG_ANY_SPECIFIED=false
FLAG_AVRCP=false
FLAG_BLUETOOTH=false
FLAG_MUSIC_APK=false
FLAG_REMOVE_APPS=false
FLAG_ROOT=false
PATH_ARTIFACTS=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --artifacts-dir)
      PATH_ARTIFACTS="$2"
      shift 2
      ;;
    --adb)
      FLAG_ADB=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    --avrcp)
      FLAG_AVRCP=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    --bluetooth)
      FLAG_BLUETOOTH=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    --music-apk)
      FLAG_MUSIC_APK=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    --remove-apps)
      FLAG_REMOVE_APPS=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    --root)
      FLAG_ROOT=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    --all)
      FLAG_AVRCP=true
      FLAG_BLUETOOTH=true
      FLAG_ADB=true
      FLAG_MUSIC_APK=true
      FLAG_REMOVE_APPS=true
      FLAG_ROOT=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "Error: Unknown option '$1'"
      echo ""
      show_help
      exit 1
      ;;
  esac
done

# Validate mandatory --artifacts-dir flag
if [[ -z "$PATH_ARTIFACTS" ]]; then
  echo "Error: --artifacts-dir is mandatory and must be specified"
  echo ""
  show_help
  exit 1
fi

# If no patching flags specified, show help
if [[ "$FLAG_ANY_SPECIFIED" == false ]]; then
  show_help
  exit 0
fi

# All current flags affect system.img (--root installs into /system/xbin),
# so this is effectively redundant with FLAG_ANY_SPECIFIED. Kept as a separate
# variable so a future boot.img-only flag is a one-line gate change.
FLAG_ANY_SYSTEM_PATCH=false
if [[ "$FLAG_ADB" == true || "$FLAG_AVRCP" == true || "$FLAG_BLUETOOTH" == true || "$FLAG_MUSIC_APK" == true || "$FLAG_REMOVE_APPS" == true || "$FLAG_ROOT" == true ]]; then
  FLAG_ANY_SYSTEM_PATCH=true
fi

# Prompt for sudo only if we'll need it (mounting system.img).
if [[ "$FLAG_ANY_SYSTEM_PATCH" == true ]]; then
  echo "This script requires sudo for mounting and file operations."
  sudo -v
  while true; do sudo -n true; sleep 50; kill -0 "$$" 2>/dev/null || exit; done 2>/dev/null &
  SUDO_KEEPALIVE_PID=$!
  # Cleanup (sudo keepalive + tempdir) is registered later via a composite
  # trap; see _cleanup below.
fi

# Version-independent constants
FILENAME_ROM_ZIP="rom.zip"
FILENAME_SYSTEM_IMAGE_BASENAME="system.img"
FILENAME_BUILD_PROP="build.prop"
FILENAME_Y1_MEDIA_BRIDGE_APK="Y1MediaBridge.apk"

# Version-dependent constants (set after stock MD5 validation)
VERSION_FIRMWARE=""
FILENAME_SYSTEM_IMAGE_TARGET=""
FILENAME_MUSIC_APK=""

# Manifest of known stock firmware builds. Each row has five pipe-delimited
# fields: <version>|<system.img md5>|<boot.img md5>|<rom.zip md5>|<music APK filename>.
# system.img md5 is the RAW (post-simg2img) hash — sparse inputs are
# de-sparsed into a working copy first and the working copy is what's matched.
# rom.zip md5 is documentation-only (the bash does not consume rom.zip).
KNOWN_FIRMWARES=(
  "3.0.2|473991dadeb1a8c4d25902dee9ee362b|1f7920228a20c01ad274c61c94a8cf36|82657db82578a38c6f1877e02407127a|com.innioasis.y1_3.0.2.apk"
)

PATH_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PATH_MOUNT="/mnt/y1-devel"
PATH_MTKCLIENT="/opt/mtkclient-2.1.4.1"
PATH_VENV_MTKCLIENT="/opt/venv/mtkclient"

# Cross-platform MD5: md5sum on Linux, md5 -q on macOS.
md5_of() {
  if command -v md5sum >/dev/null 2>&1; then
    md5sum "$1" | awk '{print $1}'
  elif command -v md5 >/dev/null 2>&1; then
    md5 -q "$1"
  else
    echo "ERROR: neither md5sum nor md5 in PATH — cannot validate stock images" >&2
    exit 1
  fi
}

# resolve_version <kind: system|boot|rom> <md5> — echos matching firmware
# version on stdout, returns 1 on no match.
resolve_version() {
  local kind="$1" md5="$2" idx
  case "$kind" in
    system) idx=1 ;;
    boot)   idx=2 ;;
    rom)    idx=3 ;;
    *) return 2 ;;
  esac
  local row parts
  for row in "${KNOWN_FIRMWARES[@]}"; do
    IFS='|' read -ra parts <<< "$row"
    if [[ "${parts[$idx]}" == "$md5" ]]; then
      echo "${parts[0]}"
      return 0
    fi
  done
  return 1
}

# firmware_field <version> <field: system_md5|boot_md5|rom_md5|music_apk>
# — echos the requested field for the given version, or returns 1 if version
# is unknown.
firmware_field() {
  local version="$1" field="$2" idx
  case "$field" in
    system_md5) idx=1 ;;
    boot_md5)   idx=2 ;;
    rom_md5)    idx=3 ;;
    music_apk)  idx=4 ;;
    *) return 2 ;;
  esac
  local row parts
  for row in "${KNOWN_FIRMWARES[@]}"; do
    IFS='|' read -ra parts <<< "$row"
    if [[ "${parts[0]}" == "$version" ]]; then
      echo "${parts[$idx]}"
      return 0
    fi
  done
  return 1
}

print_known_firmwares() {
  echo "Known stock firmware MD5s (manifest in innioasis-y1-fixes.bash):" >&2
  local row parts
  for row in "${KNOWN_FIRMWARES[@]}"; do
    IFS='|' read -ra parts <<< "$row"
    echo "  v${parts[0]}:" >&2
    echo "    system.img:  ${parts[1]}  (raw / post-simg2img)" >&2
    echo "    boot.img:    ${parts[2]}" >&2
    echo "    rom.zip:     ${parts[3]}  (reference only, not consumed)" >&2
    echo "    music APK:   app/${parts[4]}" >&2
  done
}

# Tempdir for staging stock binaries extracted from the mount and the
# corresponding patched output before writing back.
PATH_TMP_STAGE="$(mktemp -d -t y1-fixes.XXXXXX)"

# Composite cleanup trap: keep any existing SUDO_KEEPALIVE_PID kill plus
# tempdir removal. Re-set after the sudo block above (which installed its
# own trap) so both fire on exit.
_cleanup() {
  [[ -n "${SUDO_KEEPALIVE_PID:-}" ]] && kill "${SUDO_KEEPALIVE_PID}" 2>/dev/null
  rm -rf "${PATH_TMP_STAGE}"
}
trap _cleanup EXIT

# patch_in_place_bytes <mount-relative-path> <patch-script-name> [mode]
#
# Extract a stock binary from ${PATH_MOUNT}, run the named patch_*.py against
# it (writing to a tempdir), and write the patched bytes back into the mount
# with the requested mode and root:root ownership.
#
# If the patch script reports "already patched" (exit 0 with no output file),
# this is a no-op — the mount already has the correct bytes.
#
# Bails the whole script on patcher failure (MD5 mismatch, missing patch
# sites, etc.).
patch_in_place_bytes() {
  local mount_rel="$1"
  local script="$2"
  local mode="${3:-644}"
  local stage_dir="${PATH_TMP_STAGE}/$(basename "${mount_rel}")"
  local stock="${stage_dir}/stock"
  local patched="${stage_dir}/patched"

  mkdir -p "${stage_dir}"
  echo "  ${mount_rel}: extract → ${script} → write-back"
  sudo cp "${PATH_MOUNT}/${mount_rel}" "${stock}"
  sudo chown "$(id -u):$(id -g)" "${stock}"

  if ! python3 "${PATH_SCRIPT_DIR}/${script}" "${stock}" --output "${patched}"; then
    echo "ERROR: ${script} failed for ${mount_rel}" >&2
    exit 1
  fi

  if [[ -f "${patched}" ]]; then
    sudo cp "${patched}" "${PATH_MOUNT}/${mount_rel}"
    sudo chmod "${mode}" "${PATH_MOUNT}/${mount_rel}"
    sudo chown root:root "${PATH_MOUNT}/${mount_rel}"
  fi
  # If patched isn't there, the script said "already patched" — mount is
  # correct, no write-back needed.
}

# patch_in_place_y1_apk <mount-relative-path>
#
# Special-case wrapper for patch_y1_apk.py (script-style program, no --output
# flag, output landing in CWD/output/). Runs the patcher from PATH_SCRIPT_DIR
# so apktool.jar caches and the output APK end up there, then writes the
# patched APK back into the mount.
patch_in_place_y1_apk() {
  local mount_rel="$1"
  local stage_dir="${PATH_TMP_STAGE}/$(basename "${mount_rel}")"
  local stock="${stage_dir}/stock.apk"
  local patched="${PATH_SCRIPT_DIR}/output/com.innioasis.y1_${VERSION_FIRMWARE}-patched.apk"

  mkdir -p "${stage_dir}"
  echo "  ${mount_rel}: extract → patch_y1_apk.py → write-back"
  sudo cp "${PATH_MOUNT}/${mount_rel}" "${stock}"
  sudo chown "$(id -u):$(id -g)" "${stock}"

  if ! ( cd "${PATH_SCRIPT_DIR}" && python3 patch_y1_apk.py "${stock}" ); then
    echo "ERROR: patch_y1_apk.py failed for ${mount_rel}" >&2
    exit 1
  fi

  if [[ ! -f "${patched}" ]]; then
    echo "ERROR: patch_y1_apk.py did not produce ${patched}" >&2
    exit 1
  fi

  sudo cp "${patched}" "${PATH_MOUNT}/${mount_rel}"
  sudo chmod 644 "${PATH_MOUNT}/${mount_rel}"
  sudo chown root:root "${PATH_MOUNT}/${mount_rel}"
}

# --- Stock-firmware validation + rom.zip extraction --------------------------
#
# The official OTA rom.zip is the single firmware input. Validate its MD5
# against the KNOWN_FIRMWARES manifest (collision-resistant, so the rom.zip
# hash implies the contents), then extract only the inner files needed by the
# active flags into the tempdir. Each extracted file's MD5 is cross-verified
# against the manifest as a defensive check. system.img is then sparse-checked
# and de-sparsed via simg2img if needed (the manifest hash is for the raw
# representation; v3.0.2's bundled system.img happens to be raw, but future
# firmware versions might bundle a sparse one).

rom="${PATH_ARTIFACTS}/${FILENAME_ROM_ZIP}"
if [[ ! -f "$rom" ]]; then
  echo "ERROR: ${FILENAME_ROM_ZIP} not found in ${PATH_ARTIFACTS}" >&2
  exit 1
fi
if ! command -v unzip >/dev/null 2>&1; then
  echo "ERROR: unzip is not in PATH — required to extract from ${FILENAME_ROM_ZIP}" >&2
  exit 1
fi

echo "Validating rom.zip against stock-firmware manifest.."
rom_md5=$(md5_of "$rom")
if VERSION_FIRMWARE=$(resolve_version rom "$rom_md5"); then
  echo "  → matched v${VERSION_FIRMWARE} (rom.zip md5 ${rom_md5})"
else
  echo "ERROR: ${FILENAME_ROM_ZIP} md5 ${rom_md5} does not match any known stock firmware." >&2
  print_known_firmwares
  exit 1
fi

# Extract system.img from rom.zip (only file currently needed by any flag).
echo "Extracting from ${FILENAME_ROM_ZIP}: ${FILENAME_SYSTEM_IMAGE_BASENAME}"
if ! unzip -j -o "$rom" "${FILENAME_SYSTEM_IMAGE_BASENAME}" -d "$PATH_TMP_STAGE" >/dev/null; then
  echo "ERROR: extraction from ${FILENAME_ROM_ZIP} failed" >&2
  exit 1
fi

PATH_SYSTEM_IMG="${PATH_TMP_STAGE}/${FILENAME_SYSTEM_IMAGE_BASENAME}"

# system.img: extracted, then sparse-checked. If sparse, simg2img into a
# `system-raw.img` companion in the tempdir; the raw bytes are what we
# validate against the manifest hash.
if [[ "$FLAG_ANY_SYSTEM_PATCH" == true ]]; then
  is_sparse=false
  if command -v file >/dev/null 2>&1 && file "$PATH_SYSTEM_IMG" | grep -q "Android sparse image"; then
    is_sparse=true
  else
    magic=$(head -c 4 "$PATH_SYSTEM_IMG" 2>/dev/null | od -An -v -t x1 | tr -d ' \n')
    [[ "$magic" == "3aff26ed" ]] && is_sparse=true
  fi
  if [[ "$is_sparse" == true ]]; then
    if ! command -v simg2img >/dev/null 2>&1; then
      cat >&2 <<EOF
ERROR: extracted system.img is an Android sparse image, but simg2img is not
in PATH. Install it and re-run:
  Debian/Ubuntu:        sudo apt install android-sdk-libsparse-utils
  Arch:                 sudo pacman -S android-tools
  Fedora:               sudo dnf install android-tools
  RHEL/Rocky/Alma 8+:   sudo dnf install epel-release && sudo dnf install android-tools
  macOS (brew):         brew install simg2img
EOF
      exit 1
    fi
    echo "Extracted system.img is sparse — converting to raw via simg2img.."
    raw="${PATH_TMP_STAGE}/system-raw.img"
    simg2img "$PATH_SYSTEM_IMG" "$raw"
    PATH_SYSTEM_IMG="$raw"
  fi

  sys_md5=$(md5_of "$PATH_SYSTEM_IMG")
  expected=$(firmware_field "$VERSION_FIRMWARE" system_md5)
  if [[ "$sys_md5" != "$expected" ]]; then
    echo "ERROR: extracted system.img md5 ${sys_md5} differs from manifest v${VERSION_FIRMWARE} (expected ${expected})" >&2
    exit 1
  fi
fi

# Now we know the version — populate version-dependent filename constants.
FILENAME_SYSTEM_IMAGE_TARGET="system-${VERSION_FIRMWARE}-devel.img"
FILENAME_MUSIC_APK="$(firmware_field "$VERSION_FIRMWARE" music_apk)"

# Stage the validated raw system.img into its versioned working copy in the
# artifacts dir (so mtkclient can flash it later) and mount it.
if [[ "$FLAG_ANY_SYSTEM_PATCH" == true ]]; then
  dst="${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_TARGET}"
  cp "$PATH_SYSTEM_IMG" "$dst"
  echo "Mounting working copy of system.img.."
  sudo mount -o loop "$dst" "${PATH_MOUNT}/"
fi

# Enable ADB debugging
if [[ "$FLAG_ADB" == true ]]; then
  echo "Configuring build.prop for ADB debugging.."
  sudo tee -a "${PATH_MOUNT}/${FILENAME_BUILD_PROP}" <<EOF > /dev/null
# Modified to enable ADB debugging
persist.service.adb.enable=1
persist.service.debuggable=1
EOF
fi

# Install /system/xbin/su (setuid-root escalator)
if [[ "$FLAG_ROOT" == true ]]; then
  src_su="${PATH_SCRIPT_DIR}/su/build/su"
  if [[ ! -f "$src_su" ]]; then
    echo "ERROR: ${src_su} not found." >&2
    echo "       Build it first: cd ${PATH_SCRIPT_DIR}/su && make" >&2
    exit 1
  fi
  echo "Installing /system/xbin/su (setuid-root escalator).."
  sudo install -m 06755 -o root -g root "$src_su" "${PATH_MOUNT}/xbin/su"
fi

# Enable AVRCP 1.4 support (WIP - pending flash verification)
if [[ "$FLAG_AVRCP" == true ]]; then
  echo "Enabling AVRCP 1.4 support (WIP).."

  if [[ ! -f "${PATH_ARTIFACTS}/${FILENAME_Y1_MEDIA_BRIDGE_APK}" ]]; then
    echo "ERROR: --avrcp requires ${FILENAME_Y1_MEDIA_BRIDGE_APK} in ${PATH_ARTIFACTS}" >&2
    exit 1
  fi

  echo "  Installing Y1MediaBridge.apk (externally built — copied from artifacts).."
  sudo cp "${PATH_ARTIFACTS}/${FILENAME_Y1_MEDIA_BRIDGE_APK}" "${PATH_MOUNT}/app/"
  sudo chmod 644 "${PATH_MOUNT}/app/${FILENAME_Y1_MEDIA_BRIDGE_APK}"
  sudo chown root:root "${PATH_MOUNT}/app/${FILENAME_Y1_MEDIA_BRIDGE_APK}"

  patch_in_place_bytes "app/MtkBt.odex"          "patch_mtkbt_odex.py"        644
  patch_in_place_bytes "bin/mtkbt"               "patch_mtkbt.py"             755
  patch_in_place_bytes "lib/libextavrcp.so"      "patch_libextavrcp.py"       644
  patch_in_place_bytes "lib/libextavrcp_jni.so"  "patch_libextavrcp_jni.py"   644
fi

# Configure Bluetooth fixes
if [[ "$FLAG_BLUETOOTH" == true ]]; then
  echo "Configuring Bluetooth fixes.."
  sudo sed -i 's/^Enable=.*/Enable=Source,Control,Target/' "${PATH_MOUNT}/etc/bluetooth/audio.conf"
  sudo sed -i 's/^Master=.*/Master=true/' "${PATH_MOUNT}/etc/bluetooth/audio.conf"
  sudo sed -i 's/^AddressBlacklist=.*/AddressBlacklist=/' "${PATH_MOUNT}/etc/bluetooth/auto_pairing.conf"
  sudo sed -i 's/^ExactNameBlacklist=.*/ExactNameBlacklist=/' "${PATH_MOUNT}/etc/bluetooth/auto_pairing.conf"
  sudo sed -i 's/^PartialNameBlacklist=.*/PartialNameBlacklist=/' "${PATH_MOUNT}/etc/bluetooth/auto_pairing.conf"
  sudo sed -i '/^scoSocket/d' "${PATH_MOUNT}/etc/bluetooth/blacklist.conf"

  echo "Configuring build.prop for Bluetooth fixes.."
  sudo tee -a "${PATH_MOUNT}/${FILENAME_BUILD_PROP}" <<EOF > /dev/null
# Modified to properly configure Bluetooth
persist.bluetooth.avrcpversion=avrcp14
ro.bluetooth.class=2098204
ro.bluetooth.profiles.a2dp.source.enabled=true
ro.bluetooth.profiles.avrcp.target.enabled=true
EOF
fi

# Patch Y1 music player APK (Artist→Album navigation)
if [[ "$FLAG_MUSIC_APK" == true ]]; then
  echo "Patching Y1 music player APK.."
  patch_in_place_y1_apk "app/${FILENAME_MUSIC_APK}"
fi

# Remove unnecessary APK files
if [[ "$FLAG_REMOVE_APPS" == true ]]; then
  echo "Removing unnecessary APK files.."
  apps_to_remove=(
    "ApplicationGuide.*"
    "BackupRestoreConfirmation.*"
    "BasicDreams.*"
    "Calendar*"
    "CellConnService.*"
    "DataTransfer.*"
    "FusedLocation.*"
    "MemClear.*"
    "MtkWorldClockWidget.*"
    "Nfc.*"
    "PhotoTable.*"
    "PicoTts.*"
    "Protips.*"
    # "SchedulePowerOnOff.*"
    "SharedStorageBackup.*"
    "TelephonyProvider.*"
    "UserDictionaryProvider.*"
    "VpnDialogs.*"
  )

  for app in "${apps_to_remove[@]}"; do
    sudo rm -rf "${PATH_MOUNT}/app/${app}"
  done
fi

# Unmount patched system.img (only if we mounted it).
if [[ "$FLAG_ANY_SYSTEM_PATCH" == true ]]; then
  echo "Unmounting development system.img.."
  sudo umount "${PATH_MOUNT}"
fi

# Change directories to MTK Client root directory
echo "Changing directories to MTK Client root directory.."
cd "${PATH_MTKCLIENT}"

# Activate MTKClient venv
echo "Activating MTKClient Python virtual environment.."
source "${PATH_VENV_MTKCLIENT}/bin/activate"

# Write patched system.img only if we patched one.
if [[ "$FLAG_ANY_SYSTEM_PATCH" == true ]]; then
  echo "Writing new system.img (plug in and reset Y1 device using button near USB-C port).."
  python3 "${PATH_MTKCLIENT}/mtk.py" w android "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_TARGET}"
fi

echo "Deactivating MTKClient Python virtual environment.."
deactivate
echo "Done!"
