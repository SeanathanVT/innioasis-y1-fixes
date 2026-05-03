#!/usr/bin/env bash
#
# Script: innioasis-y1-fixes.bash
# Description: Patches Innioasis Y1 system.img to fix Bluetooth AVRCP, remove APK-related cruft, and enable ADB debugging.
# Author: Sean Halpin (github.com/SeanathanVT)
# Version: 1.3.1
# History:
# 2026-05-03 (1.3.1): --root no longer touches system.img (skip copy/mount/patch/unmount/flash and the sudo prompt unless a system-affecting flag is set). --root help text now warns against running `adb root` post-flash (stock MTK adbd's USB re-bind is flaky; with ro.secure=0 adbd is already uid 0).
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

--artifacts-dir <path> is mandatory and specifies the directory containing binary files and artifacts.

If only --artifacts-dir is specified, this help message is displayed.
If any patching option is specified, the script will mount the system.img, apply the selected
patches, and then write the patched system.img to the device.

MANDATORY:
  --artifacts-dir <path> Directory containing binary files and APKs

OPTIONS:
  --adb                Enable ADB debugging
  --avrcp              Enable AVRCP 1.4 support (WIP - pending flash verification)
  --bluetooth          Configure Bluetooth fixes
  --music-apk          Copy patched Y1 music player APK
  --remove-apps        Remove unnecessary APK files from system
  --root               Patch boot.img ramdisk for ADB root access (default.prop:
                       ro.secure=0, ro.debuggable=1, ro.adb.secure=0). Requires
                       boot.img in --artifacts-dir. After flashing, 'adb shell'
                       returns uid 0 directly — do NOT run 'adb root' (its
                       restart triggers a stock MTK adbd USB re-bind that loses
                       the connection on this firmware; reboot to recover).
                       When --root is the only flag, system.img is left alone.
  --all                Apply all patches (equivalent to all flags above)
  -h, --help           Display this help message

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

# Determine whether any system.img-affecting flag is set. --root only patches
# boot.img and does not require mounting system.img.
FLAG_ANY_SYSTEM_PATCH=false
if [[ "$FLAG_ADB" == true || "$FLAG_AVRCP" == true || "$FLAG_BLUETOOTH" == true || "$FLAG_MUSIC_APK" == true || "$FLAG_REMOVE_APPS" == true ]]; then
  FLAG_ANY_SYSTEM_PATCH=true
fi

# Prompt for sudo only if we'll need it (mounting system.img). --root alone
# uses pure-Python boot.img patching + mtkclient and does not need sudo.
if [[ "$FLAG_ANY_SYSTEM_PATCH" == true ]]; then
  echo "This script requires sudo for mounting and file operations."
  sudo -v
  while true; do sudo -n true; sleep 50; kill -0 "$$" 2>/dev/null || exit; done 2>/dev/null &
  SUDO_KEEPALIVE_PID=$!
  trap 'kill "${SUDO_KEEPALIVE_PID}" 2>/dev/null' EXIT
fi

VERSION_FIRMWARE="3.0.2"

FILENAME_BIN_MTKBT="mtkbt"
FILENAME_BIN_MTKBT_PATCHED="mtkbt.patched"
FILENAME_BOOT_IMAGE_SOURCE="boot.img"
FILENAME_BOOT_IMAGE_TARGET="boot-${VERSION_FIRMWARE}-devel.img"
FILENAME_BUILD_PROP="build.prop"
FILENAME_LIBRARY_LIBEXTAVRCP="libextavrcp.so"
FILENAME_LIBRARY_LIBEXTAVRCP_PATCHED="libextavrcp.so.patched"
FILENAME_LIBRARY_LIBEXTAVRCP_JNI="libextavrcp_jni.so"
FILENAME_LIBRARY_LIBEXTAVRCP_JNI_PATCHED="libextavrcp_jni.so.patched"
FILENAME_MTKBT_ODEX="MtkBt.odex"
FILENAME_MTKBT_ODEX_PATCHED="MtkBt.odex.patched"
FILENAME_MUSIC_APK="com.innioasis.y1_${VERSION_FIRMWARE}.apk"
FILENAME_MUSIC_APK_PATCHED="com.innioasis.y1_${VERSION_FIRMWARE}-patched.apk"
FILENAME_SYSTEM_IMAGE_SOURCE="system.img"
FILENAME_SYSTEM_IMAGE_TARGET="system-${VERSION_FIRMWARE}-devel.img"
FILENAME_Y1_MEDIA_BRIDGE_APK="Y1MediaBridge.apk"

PATH_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PATH_MOUNT="/mnt/y1-devel"
PATH_MTKCLIENT="/opt/mtkclient-2.1.4.1"
PATH_VENV_MTKCLIENT="/opt/venv/mtkclient"

# Patch boot.img ramdisk for ADB root access
if [[ "$FLAG_ROOT" == true ]]; then
  if [[ ! -f "${PATH_ARTIFACTS}/${FILENAME_BOOT_IMAGE_SOURCE}" ]]; then
    echo "Error: --root requires ${FILENAME_BOOT_IMAGE_SOURCE} in ${PATH_ARTIFACTS}"
    exit 1
  fi
  echo "Patching boot.img ramdisk for ADB root access.."
  python3 "${PATH_SCRIPT_DIR}/patch_bootimg.py" \
    --in  "${PATH_ARTIFACTS}/${FILENAME_BOOT_IMAGE_SOURCE}" \
    --out "${PATH_ARTIFACTS}/${FILENAME_BOOT_IMAGE_TARGET}"
fi

# Copy and mount system.img only if a system-affecting flag is set.
if [[ "$FLAG_ANY_SYSTEM_PATCH" == true ]]; then
  echo "Copying clean system.img.."
  cp "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_SOURCE}" "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_TARGET}"

  echo "Mounting working copy of system.img.."
  sudo mount -o loop "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_TARGET}" "${PATH_MOUNT}/"
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

# Enable AVRCP 1.4 support (WIP - pending flash verification)
if [[ "$FLAG_AVRCP" == true ]]; then
  echo "Enabling AVRCP 1.4 support (WIP).."

  echo "  Copying Y1 Media Bridge APK.."
  sudo cp "${PATH_ARTIFACTS}/${FILENAME_Y1_MEDIA_BRIDGE_APK}" "${PATH_MOUNT}/app/"
  sudo chmod 644 "${PATH_MOUNT}/app/${FILENAME_Y1_MEDIA_BRIDGE_APK}"
  sudo chown root:root "${PATH_MOUNT}/app/${FILENAME_Y1_MEDIA_BRIDGE_APK}"

  echo "  Copying patched MtkBt odex.."
  sudo cp "${PATH_ARTIFACTS}/${FILENAME_MTKBT_ODEX_PATCHED}" "${PATH_MOUNT}/app/${FILENAME_MTKBT_ODEX}"
  sudo chmod 644 "${PATH_MOUNT}/app/${FILENAME_MTKBT_ODEX}"
  sudo chown root:root "${PATH_MOUNT}/app/${FILENAME_MTKBT_ODEX}"

  echo "  Copying patched mtkbt binary.."
  sudo cp "${PATH_ARTIFACTS}/${FILENAME_BIN_MTKBT_PATCHED}" "${PATH_MOUNT}/bin/${FILENAME_BIN_MTKBT}"
  sudo chmod 755 "${PATH_MOUNT}/bin/${FILENAME_BIN_MTKBT}"
  sudo chown root:root "${PATH_MOUNT}/bin/${FILENAME_BIN_MTKBT}"

  echo "  Copying patched AVRCP library.."
  sudo cp "${PATH_ARTIFACTS}/${FILENAME_LIBRARY_LIBEXTAVRCP_PATCHED}" "${PATH_MOUNT}/lib/${FILENAME_LIBRARY_LIBEXTAVRCP}"
  sudo chmod 644 "${PATH_MOUNT}/lib/${FILENAME_LIBRARY_LIBEXTAVRCP}"
  sudo chown root:root "${PATH_MOUNT}/lib/${FILENAME_LIBRARY_LIBEXTAVRCP}"

  echo "  Copying patched AVRCP JNI library.."
  sudo cp "${PATH_ARTIFACTS}/${FILENAME_LIBRARY_LIBEXTAVRCP_JNI_PATCHED}" "${PATH_MOUNT}/lib/${FILENAME_LIBRARY_LIBEXTAVRCP_JNI}"
  sudo chmod 644 "${PATH_MOUNT}/lib/${FILENAME_LIBRARY_LIBEXTAVRCP_JNI}"
  sudo chown root:root "${PATH_MOUNT}/lib/${FILENAME_LIBRARY_LIBEXTAVRCP_JNI}"
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

# Copy patched Y1 music player APK
if [[ "$FLAG_MUSIC_APK" == true ]]; then
  echo "Copying patched Y1 music player APK.."
  sudo cp "${PATH_ARTIFACTS}/${FILENAME_MUSIC_APK_PATCHED}" "${PATH_MOUNT}/app/${FILENAME_MUSIC_APK}"
  sudo chmod 644 "${PATH_MOUNT}/app/${FILENAME_MUSIC_APK}"
  sudo chown root:root "${PATH_MOUNT}/app/${FILENAME_MUSIC_APK}"
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

# Write patched boot.img
if [[ "$FLAG_ROOT" == true ]]; then
  echo "Writing new boot.img (plug in and reset Y1 device using button near USB-C port).."
  python3 "${PATH_MTKCLIENT}/mtk.py" w bootimg "${PATH_ARTIFACTS}/${FILENAME_BOOT_IMAGE_TARGET}"
fi

echo "Deactivating MTKClient Python virtual environment.."
deactivate
echo "Done!"
