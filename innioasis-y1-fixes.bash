#!/usr/bin/env bash
#
# Script: innioasis-y1-fixes.bash
# Description: Patches Innioasis Y1 system.img file to fix Bluetooth AVRCP and remove APK-related cruft.
# Author: Sean Halpin (github.com/SeanathanVT)
# Version: 1.0.8
# History:
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
If any patching option is specified, the script will mount the system.img, apply the selected patches,
and then write the patched system.img to the device.

MANDATORY:
  --artifacts-dir <path> Directory containing binary files and APKs

OPTIONS:
  --avrcp              Copy support files for AVRCP fix (Y1 Media Bridge, MtkBt odex, AVRCP JNI, mtkbt binary)
  --bluetooth          Configure Bluetooth settings
  --build-prop         Configure build.prop for ADB and Bluetooth
  --music-apk          Copy patched Y1 music player APK
  --remove-apps        Remove unnecessary APK files from system
  --all                Apply all patches (equivalent to all flags above)
  -h, --help           Display this help message

EXAMPLES:
  # Apply all patches
  ./innioasis-y1-fixes.bash --artifacts-dir /path/to/artifacts --all

  # Apply only specific patches
  ./innioasis-y1-fixes.bash --artifacts-dir /path/to/artifacts --music-apk --bluetooth --remove-apps

EOF
}

# Initialize flags
FLAG_ANY_SPECIFIED=false
FLAG_AVRCP=false
FLAG_BLUETOOTH=false
FLAG_BUILD_PROP=false
FLAG_MUSIC_APK=false
FLAG_REMOVE_APPS=false
PATH_ARTIFACTS=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --artifacts-dir)
      PATH_ARTIFACTS="$2"
      shift 2
      ;;
    --music-apk)
      FLAG_MUSIC_APK=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    --avrcp)
      FLAG_AVRCP=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    --build-prop)
      FLAG_BUILD_PROP=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    --bluetooth)
      FLAG_BLUETOOTH=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    --remove-apps)
      FLAG_REMOVE_APPS=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    --all)
      FLAG_AVRCP=true
      FLAG_MUSIC_APK=true
      FLAG_BLUETOOTH=true
      FLAG_BUILD_PROP=true
      FLAG_REMOVE_APPS=true
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

VERSION_FIRMWARE="3.0.2"

FILENAME_BIN_MTKBT="mtkbt"
FILENAME_BUILD_PROP="build.prop"
FILENAME_LIBRARY_LIBEXTAVRCP_JNI="libextavrcp_jni.so"
FILENAME_MTKBT_APK="MtkBt.apk"
FILENAME_MUSIC_APK="com.innioasis.y1_${VERSION_FIRMWARE}.apk"
FILENAME_MUSIC_APK_PATCHED="com.innioasis.y1_${VERSION_FIRMWARE}-patched.apk"
FILENAME_MTKBT_ODEX="MtkBt.odex"
FILENAME_SYSTEM_IMAGE_SOURCE="system.img"
FILENAME_SYSTEM_IMAGE_TARGET="system-${VERSION_FIRMWARE}-devel.img"
FILENAME_Y1_MEDIA_BRIDGE_APK="Y1MediaBridge.apk"

PATH_MOUNT="/mnt/y1-devel"
PATH_MTKCLIENT="/opt/mtkclient-2.1.4.1"
PATH_VENV_MTKCLIENT="/opt/venv/mtkclient"

# Copy clean system.img and mount (always runs when any flag is specified)
echo "Copying clean system.img.."
cp "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_SOURCE}" "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_TARGET}"

# Mount working copy of system.img
echo "Mounting working copy of system.img.."
sudo mount -o loop "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_TARGET}" "${PATH_MOUNT}/"

# Copy patched Y1 music player APK
if [[ "$FLAG_MUSIC_APK" == true ]]; then
  echo "Copying patched Y1 music player APK.."
  sudo cp "${PATH_ARTIFACTS}/${FILENAME_MUSIC_APK_PATCHED}" "${PATH_MOUNT}/app/${FILENAME_MUSIC_APK}"
  sudo chmod 644 "${PATH_MOUNT}/app/${FILENAME_MUSIC_APK}"
  sudo chown root:root "${PATH_MOUNT}/app/${FILENAME_MUSIC_APK}"
fi

# Copy support files for AVRCP fix (Y1 Media Bridge APK, MtkBt odex, AVRCP JNI, mtkbt binary)
if [[ "$FLAG_AVRCP" == true ]]; then
  echo "Copying Y1 Media Bridge APK.."
  sudo cp "${PATH_ARTIFACTS}/${FILENAME_Y1_MEDIA_BRIDGE_APK}" "${PATH_MOUNT}/app/"
  sudo chmod 644 "${PATH_MOUNT}/app/${FILENAME_Y1_MEDIA_BRIDGE_APK}"
  sudo chown root:root "${PATH_MOUNT}/app/${FILENAME_Y1_MEDIA_BRIDGE_APK}"

  echo "Copying patched MtkBt odex.."
  sudo cp "${PATH_ARTIFACTS}/${FILENAME_MTKBT_ODEX}" "${PATH_MOUNT}/app/${FILENAME_MTKBT_ODEX}"
  sudo chmod 644 "${PATH_MOUNT}/app/${FILENAME_MTKBT_ODEX}"
  sudo chown root:root "${PATH_MOUNT}/app/${FILENAME_MTKBT_ODEX}"

  echo "Copying patched AVRCP JNI library.."
  sudo cp "${PATH_ARTIFACTS}/${FILENAME_LIBRARY_LIBEXTAVRCP_JNI}" "${PATH_MOUNT}/lib/${FILENAME_LIBRARY_LIBEXTAVRCP_JNI}"
  sudo chmod 644 "${PATH_MOUNT}/lib/${FILENAME_LIBRARY_LIBEXTAVRCP_JNI}"
  sudo chown root:root "${PATH_MOUNT}/lib/${FILENAME_LIBRARY_LIBEXTAVRCP_JNI}"

  echo "Copying patched mtkbt binary.."
  sudo cp "${PATH_ARTIFACTS}/${FILENAME_BIN_MTKBT}" "${PATH_MOUNT}/bin/${FILENAME_BIN_MTKBT}"
  sudo chmod 755 "${PATH_MOUNT}/bin/${FILENAME_BIN_MTKBT}"
  sudo chown root:root "${PATH_MOUNT}/bin/${FILENAME_BIN_MTKBT}"
fi

# Configure build.prop
if [[ "$FLAG_BUILD_PROP" == true ]]; then
  echo "Configuring build.prop.."
  sudo tee -a "${PATH_MOUNT}/${FILENAME_BUILD_PROP}" <<EOF > /dev/null
# Modified to fix ADB / Bluetooth
persist.bluetooth.avrcpversion=avrcp13
persist.service.adb.enable=1
persist.service.debuggable=1
ro.bluetooth.class=2098204
ro.bluetooth.profiles.a2dp.source.enabled=true
ro.bluetooth.profiles.avrcp.target.enabled=true
EOF
fi

# Configure Bluetooth
if [[ "$FLAG_BLUETOOTH" == true ]]; then
  echo "Configuring Bluetooth.."
  sudo sed -i 's/^Enable=.*/Enable=Source,Control,Target/' "${PATH_MOUNT}/etc/bluetooth/audio.conf"
  sudo sed -i 's/^Master=.*/Master=true/' "${PATH_MOUNT}/etc/bluetooth/audio.conf"
  sudo sed -i 's/^AddressBlacklist=.*/AddressBlacklist=/' "${PATH_MOUNT}/etc/bluetooth/auto_pairing.conf"
  sudo sed -i 's/^ExactNameBlacklist=.*/ExactNameBlacklist=/' "${PATH_MOUNT}/etc/bluetooth/auto_pairing.conf"
  sudo sed -i 's/^PartialNameBlacklist=.*/PartialNameBlacklist=/' "${PATH_MOUNT}/etc/bluetooth/auto_pairing.conf"
  sudo sed -i '/^scoSocket/d' "${PATH_MOUNT}/etc/bluetooth/blacklist.conf"
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

# Unmount patched system.img
echo "Unmounting development system.img.."
sudo umount "${PATH_MOUNT}"

# Change directories to MTK Client root directory
echo "Changing directories to MTK Client root directory.."
cd "${PATH_MTKCLIENT}"

# Activate MTKClient venv
echo "Activating MTKClient Python virtual environment.."
source "${PATH_VENV_MTKCLIENT}/bin/activate"

# Write patched system.img
echo "Writing new system.img (plug in and reset Y1 device using button near USB-C port).."
python3 "${PATH_MTKCLIENT}/mtk.py" w android "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_TARGET}"

echo "Deactivating MTKClient Python virtual environment.."
deactivate
echo "Done!"
