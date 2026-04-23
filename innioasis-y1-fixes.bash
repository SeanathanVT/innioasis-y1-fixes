#!/usr/bin/env bash
#
# Script: innioasis-y1-fixes.bash
# Description: Patches Innioasis Y1 system.img file to fix Bluetooth AVRCP and remove APK-related cruft.
# Author: Sean Halpin (github.com/SeanathanVT)
# Version: 1.0.4
# History:
# 2026-04-23 (1.0.4): Use unmodified (non-sparse) system.img source.
# 2026-04-23 (1.0.3): Add explicit Python virtual environment activation / deactivation.
# 2026-04-23 (1.0.2): Convert app removal to loop because it looks prettier.
# 2026-04-23 (1.0.1): Append to build.prop, do not overwrite (oops).
# 2026-04-23 (1.0.0): Initial release.
# Usage: ./innioasis-y1-fixes.bash
#

VERSION_FIRMWARE="3.0.2"

FILENAME_BIN_MTKBT="mtkbt"
FILENAME_BUILD_PROP="build.prop"
FILENAME_LIBRARY_LIBEXTAVRCP_JNI="libextavrcp_jni.so"
FILENAME_SYSTEM_IMAGE_SOURCE="system.img"
FILENAME_SYSTEM_IMAGE_TARGET="system-${VERSION_FIRMWARE}-devel.img"
FILENAME_Y1_MEDIA_BRIDGE_APK="Y1MediaBridge.apk"

PATH_ARTIFACTS="/home/sphalpin/Downloads"
PATH_MOUNT="/mnt/y1-devel"
PATH_MTKCLIENT="/opt/mtkclient-2.1.4.1"
PATH_VENV_MTKCLIENT="/opt/venv/mtkclient"

# Copy clean system.img
echo "Copying clean system.img.."
cp "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_SOURCE}" "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_TARGET}"

# Mount working copy of system.img
echo "Mounting working copy of system.img.."
sudo mount -o loop "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_TARGET}" "${PATH_MOUNT}/"

# Copy Y1 Media Bridge APK
echo "Copying Y1 Media Bridge APK.."
sudo cp "${PATH_ARTIFACTS}/${FILENAME_Y1_MEDIA_BRIDGE_APK}" "${PATH_MOUNT}/app/"
sudo chmod 644 "${PATH_MOUNT}/app/${FILENAME_Y1_MEDIA_BRIDGE_APK}"
sudo chown root:root "${PATH_MOUNT}/app/${FILENAME_Y1_MEDIA_BRIDGE_APK}"

# Copy patched AVRCP JNI library
echo "Copying patched AVRCP JNI library.."
sudo cp "${PATH_ARTIFACTS}/${FILENAME_LIBRARY_LIBEXTAVRCP_JNI}" "${PATH_MOUNT}/lib/${FILENAME_LIBRARY_LIBEXTAVRCP_JNI}"
sudo chmod 644 "${PATH_MOUNT}/lib/${FILENAME_LIBRARY_LIBEXTAVRCP_JNI}"
sudo chown root:root "${PATH_MOUNT}/lib/${FILENAME_LIBRARY_LIBEXTAVRCP_JNI}"

# Copy patched mtkbt binary
echo "Copying patched mtkbt binary.."
sudo cp "${PATH_ARTIFACTS}/${FILENAME_BIN_MTKBT}" "${PATH_MOUNT}/bin/${FILENAME_BIN_MTKBT}"
sudo chmod 755 "${PATH_MOUNT}/bin/${FILENAME_BIN_MTKBT}"
sudo chown root:root "${PATH_MOUNT}/bin/${FILENAME_BIN_MTKBT}"

# Configure build.prop
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

# Configure Bluetooth
echo "Configuring Bluetooth.."
sudo sed -i 's/^Enable=.*/Enable=Source,Control,Target/' "${PATH_MOUNT}/etc/bluetooth/audio.conf"
sudo sed -i 's/^Master=.*/Master=true/' "${PATH_MOUNT}/etc/bluetooth/audio.conf"
sudo sed -i 's/^AddressBlacklist=.*/AddressBlacklist=/' "${PATH_MOUNT}/etc/bluetooth/auto_pairing.conf"
sudo sed -i 's/^ExactNameBlacklist=.*/ExactNameBlacklist=/' "${PATH_MOUNT}/etc/bluetooth/auto_pairing.conf"
sudo sed -i 's/^PartialNameBlacklist=.*/PartialNameBlacklist=/' "${PATH_MOUNT}/etc/bluetooth/auto_pairing.conf"
sudo sed -i '/^scoSocket/d' "${PATH_MOUNT}/etc/bluetooth/blacklist.conf"

# Remove unnecessary APK files
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

# Unmount patched system.img
echo "Unmounting development system.img.."
sudo umount "${PATH_MOUNT}"

# Change directories to MTK Client root directory 
echo "Changing directories to MTK Client root directory.."
cd "${PATH_MTKCLIENT}"

# Write patched system.img
echo "Writing new system.img (plug in and reset Y1 device using button near USB-C port).."
source "${PATH_VENV_MTKCLIENT}/bin/activate"
python3 "${PATH_MTKCLIENT}/mtk.py" w android "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_TARGET}"
deactivate

