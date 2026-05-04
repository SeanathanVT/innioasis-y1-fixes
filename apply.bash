#!/usr/bin/env bash
#
# apply.bash — Koensayr (Innioasis Y1 system.img patcher).
#
# Compatibility is defined by the KNOWN_FIRMWARES manifest (rom.zip MD5).
# Add a row to support a new build.
#
# Author:    Sean Halpin (github.com/SeanathanVT)
# Version:   1.10.0
# Changelog: see CHANGELOG.md
# Patches:   see docs/PATCHES.md
#

show_help() {
  cat <<EOF
Usage: ./apply.bash [--artifacts-dir <path>] [FLAGS] [TOOLING]

Stage rom.zip in ./staging/ (default) or pass --artifacts-dir <path>
pointing at a directory containing rom.zip (validated against
KNOWN_FIRMWARES MD5 manifest). Then pick one or more of the flags below.
Run tools/setup.sh once first to clone MTKClient and create the patcher
venv.

FLAGS:
  --adb          Set persist.service.adb.enable / debuggable in build.prop
  --avrcp        KNOWN BROKEN. Patches the AVRCP 1.4 binaries + installs
                 Y1MediaBridge.apk. Empirically regresses stock AVRCP 1.0
                 PASSTHROUGH (play/pause from car/headset stops working) and
                 does not deliver 1.4 metadata as intended — mtkbt's compiled
                 AVRCP layer is 1.0-only and the byte-patch path can shape
                 the SDP advertisement but cannot make the daemon process
                 1.3+ commands. See INVESTIGATION.md "2026-05-04 conclusion"
                 and the Diagnostics section of README.md. Excluded from
                 --all. Available as an opt-in for the user-space proxy
                 work that aims to fix the underlying issue. Build first:
                   cd src/Y1MediaBridge && ./gradlew --stop && ./gradlew assembleDebug
  --bluetooth    Configure audio.conf + auto_pairing.conf + blacklist.conf
                 + build.prop entries that are essential for car/peer pairing.
                 Does NOT set persist.bluetooth.avrcpversion — that property
                 is dropped pending the AVRCP wire-protocol work.
  --music-apk    Patch the Y1 music player APK (Artist→Album navigation)
  --remove-apps  Remove bloatware APKs (ApplicationGuide, BasicDreams, …)
  --root         Install /system/xbin/su (06755 root:root). Build first:
                 cd src/su && make
  --all          --adb + --bluetooth + --music-apk + --remove-apps + --root.
                 NOT --avrcp (see warning above).
  -h, --help     This help

TOOLING (override tools/ defaults; useful if you have these installed
elsewhere or are testing alternate builds):
  --mtkclient-dir <path>   Path to a MTKClient checkout (with venv/ inside).
                            Default: tools/mtkclient/. Or set MTKCLIENT_DIR.
  --python-venv <path>     Path to a Python venv with patcher deps
                            (androguard). Default: tools/python-venv/.

Quick example:
  cp /path/to/rom.zip ./staging/
  ./apply.bash --all

For details on the patches applied by each flag, see README.md and docs/PATCHES.md.
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

# Tooling overrides — explicit flag wins over the tools/ default.
OVERRIDE_MTKCLIENT_DIR=""
OVERRIDE_PYTHON_VENV=""

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
      echo "WARNING: --avrcp is known-broken on this device. It regresses stock"
      echo "         AVRCP 1.0 PASSTHROUGH (play/pause stops working from car/"
      echo "         headset) without delivering the AVRCP 1.4 metadata it"
      echo "         intends to enable. mtkbt is internally a 1.0 implementation"
      echo "         and byte-patches cannot make it process 1.3+ commands."
      echo "         See INVESTIGATION.md 'Conclusion (2026-05-04)' for the"
      echo "         full negative result and the user-space proxy path that"
      echo "         aims to fix this. Continuing only because you asked." >&2
      echo
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
      # --avrcp is intentionally excluded — known broken; opt-in only via
      # explicit --avrcp. See apply.bash --help.
      FLAG_BLUETOOTH=true
      FLAG_ADB=true
      FLAG_MUSIC_APK=true
      FLAG_REMOVE_APPS=true
      FLAG_ROOT=true
      FLAG_ANY_SPECIFIED=true
      shift
      ;;
    --mtkclient-dir)
      OVERRIDE_MTKCLIENT_DIR="$2"
      shift 2
      ;;
    --python-venv)
      OVERRIDE_PYTHON_VENV="$2"
      shift 2
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

# --artifacts-dir falls back to ./staging/ inside the repo if not given.
# Lets the common case skip the flag: `cp rom.zip staging/ && ./apply.bash --all`.
# Power users with artifacts on a different drive / multiple firmwares keep
# passing --artifacts-dir explicitly.
PATH_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "$PATH_ARTIFACTS" ]]; then
  PATH_ARTIFACTS="${PATH_SCRIPT_DIR}/staging"
  if [[ ! -d "$PATH_ARTIFACTS" ]]; then
    echo "Error: no rom.zip staged."
    echo ""
    echo "  Either:"
    echo "    mkdir -p ${PATH_ARTIFACTS} && cp /path/to/rom.zip ${PATH_ARTIFACTS}/"
    echo "  Or:"
    echo "    ./apply.bash --artifacts-dir <your-path> [FLAGS]"
    echo ""
    show_help
    exit 1
  fi
fi

# If no patching flags specified, show help
if [[ "$FLAG_ANY_SPECIFIED" == false ]]; then
  show_help
  exit 0
fi

# Separate from FLAG_ANY_SPECIFIED so a future boot.img-only flag stays a one-line gate change.
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

# Schema: <version>|<system.img md5>|<boot.img md5>|<rom.zip md5>|<music APK filename>.
# system.img md5 is for the RAW image (post-simg2img if input was sparse).
KNOWN_FIRMWARES=(
  "3.0.2|473991dadeb1a8c4d25902dee9ee362b|1f7920228a20c01ad274c61c94a8cf36|82657db82578a38c6f1877e02407127a|com.innioasis.y1_3.0.2.apk"
)

# (PATH_SCRIPT_DIR set earlier — used by the --artifacts-dir staging fallback.)

PATH_MOUNT="/mnt/y1-devel"

# resolve_mtkclient_dir — echoes the path to the MTKClient checkout to use.
# Precedence: --mtkclient-dir flag > MTKCLIENT_DIR env var > tools/mtkclient/.
# Bails the script if none resolve to a real directory.
resolve_mtkclient_dir() {
  local p
  if [[ -n "$OVERRIDE_MTKCLIENT_DIR" ]]; then
    p="$OVERRIDE_MTKCLIENT_DIR"
  elif [[ -n "${MTKCLIENT_DIR:-}" ]]; then
    p="$MTKCLIENT_DIR"
  elif [[ -d "${PATH_SCRIPT_DIR}/tools/mtkclient" ]]; then
    p="${PATH_SCRIPT_DIR}/tools/mtkclient"
  else
    cat >&2 <<EOM
ERROR: MTKClient not found.
       Run:  ${PATH_SCRIPT_DIR}/tools/setup.sh
       Or:   --mtkclient-dir <path> / MTKCLIENT_DIR env var
EOM
    exit 1
  fi
  if [[ ! -d "$p" || ! -f "$p/mtk.py" ]]; then
    echo "ERROR: ${p} doesn't look like a MTKClient checkout (no mtk.py)" >&2
    exit 1
  fi
  echo "$p"
}

# resolve_python_venv — echoes the path to the venv to source for patcher
# invocations that need androguard, or empty string if none is configured
# (in which case the bash falls through to system python3, which is fine
# if the user has androguard pip-installed globally).
# Precedence: --python-venv flag > tools/python-venv/.
resolve_python_venv() {
  if [[ -n "$OVERRIDE_PYTHON_VENV" ]]; then
    if [[ ! -f "${OVERRIDE_PYTHON_VENV}/bin/activate" ]]; then
      echo "ERROR: --python-venv ${OVERRIDE_PYTHON_VENV} not a valid venv (missing bin/activate)" >&2
      exit 1
    fi
    echo "$OVERRIDE_PYTHON_VENV"
  elif [[ -f "${PATH_SCRIPT_DIR}/tools/python-venv/bin/activate" ]]; then
    echo "${PATH_SCRIPT_DIR}/tools/python-venv"
  else
    echo ""
  fi
}

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
  echo "Known stock firmware MD5s (manifest in apply.bash):" >&2
  local row parts
  for row in "${KNOWN_FIRMWARES[@]}"; do
    IFS='|' read -ra parts <<< "$row"
    echo "  v${parts[0]}:" >&2
    echo "    rom.zip:     ${parts[3]}  (primary input — this is what's MD5-validated)" >&2
    echo "    system.img:  ${parts[1]}  (extracted from rom.zip; raw / post-simg2img)" >&2
    echo "    boot.img:    ${parts[2]}  (in rom.zip; not consumed since v1.7.0)" >&2
    echo "    music APK:   app/${parts[4]}" >&2
  done
}

# Stages stock binaries extracted from the mount + their patched output before write-back.
PATH_TMP_STAGE="$(mktemp -d -t koensayr.XXXXXX)"

_cleanup() {
  [[ -n "${SUDO_KEEPALIVE_PID:-}" ]] && kill "${SUDO_KEEPALIVE_PID}" 2>/dev/null
  rm -rf "${PATH_TMP_STAGE}"
}
trap _cleanup EXIT

# patch_in_place_bytes <mount-rel> <patch-script-name> [mode]
# Extract stock binary → run patcher → write patched bytes back. If the patcher
# reports "already patched" (exit 0, no output file), no write-back is needed.
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

  if ! python3 "${PATH_SCRIPT_DIR}/src/patches/${script}" "${stock}" --output "${patched}"; then
    echo "ERROR: ${script} failed for ${mount_rel}" >&2
    exit 1
  fi

  if [[ -f "${patched}" ]]; then
    sudo cp "${patched}" "${PATH_MOUNT}/${mount_rel}"
    sudo chmod "${mode}" "${PATH_MOUNT}/${mount_rel}"
    sudo chown root:root "${PATH_MOUNT}/${mount_rel}"
  fi
}

# patch_in_place_y1_apk <mount-rel>
# Wrapper for patch_y1_apk.py (script-style; output lands in CWD/output/).
# Runs from src/patches/ so apktool.jar cache + output APK land there.
patch_in_place_y1_apk() {
  local mount_rel="$1"
  local stage_dir="${PATH_TMP_STAGE}/$(basename "${mount_rel}")"
  local stock="${stage_dir}/stock.apk"
  local patched="${PATH_SCRIPT_DIR}/src/patches/output/com.innioasis.y1_${VERSION_FIRMWARE}-patched.apk"

  mkdir -p "${stage_dir}"
  echo "  ${mount_rel}: extract → patch_y1_apk.py → write-back"
  sudo cp "${PATH_MOUNT}/${mount_rel}" "${stock}"
  sudo chown "$(id -u):$(id -g)" "${stock}"

  local pyvenv
  pyvenv="$(resolve_python_venv)"
  if ! (
    cd "${PATH_SCRIPT_DIR}/src/patches"
    [[ -n "$pyvenv" ]] && source "${pyvenv}/bin/activate"
    python3 patch_y1_apk.py "${stock}"
  ); then
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
# rom.zip MD5 → KNOWN_FIRMWARES → extract system.img → cross-check MD5 → de-sparse if needed.

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

# Sparse-check the extracted system.img; de-sparse via simg2img if needed.
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

# Version-dependent filenames now resolvable.
FILENAME_SYSTEM_IMAGE_TARGET="system-${VERSION_FIRMWARE}-devel.img"
FILENAME_MUSIC_APK="$(firmware_field "$VERSION_FIRMWARE" music_apk)"

# Copy validated raw system.img into the artifacts dir (mtkclient flashes from there) and mount.
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
  src_su="${PATH_SCRIPT_DIR}/src/su/build/su"
  if [[ ! -f "$src_su" ]]; then
    echo "ERROR: ${src_su} not found." >&2
    echo "       Build it first: cd ${PATH_SCRIPT_DIR}/src/su && make" >&2
    exit 1
  fi
  echo "Installing /system/xbin/su (setuid-root escalator).."
  sudo install -m 06755 -o root -g root "$src_su" "${PATH_MOUNT}/xbin/su"
fi

# Apply AVRCP 1.4 patches (KNOWN BROKEN — see warning at flag-parse and INVESTIGATION.md)
if [[ "$FLAG_AVRCP" == true ]]; then
  echo "Applying AVRCP 1.4 patches (known broken; opt-in only).."

  src_y1mb="${PATH_SCRIPT_DIR}/src/Y1MediaBridge/app/build/outputs/apk/debug/app-debug.apk"
  if [[ ! -f "$src_y1mb" ]]; then
    echo "ERROR: ${src_y1mb} not found." >&2
    echo "       Build it first: cd ${PATH_SCRIPT_DIR}/src/Y1MediaBridge && ./gradlew --stop && ./gradlew assembleDebug" >&2
    exit 1
  fi

  echo "  Installing Y1MediaBridge.apk from src/Y1MediaBridge build output.."
  sudo install -m 644 -o root -g root "$src_y1mb" "${PATH_MOUNT}/app/${FILENAME_Y1_MEDIA_BRIDGE_APK}"

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
  # persist.bluetooth.avrcpversion is intentionally NOT set — see
  # INVESTIGATION.md "Conclusion (2026-05-04)". Setting it commits to an
  # AVRCP 1.4 advertisement that mtkbt can't actually deliver, regressing
  # the working AVRCP 1.0 PASSTHROUGH. The remaining properties are
  # essential for car/peer pairing and stay regardless of AVRCP version.
  sudo tee -a "${PATH_MOUNT}/${FILENAME_BUILD_PROP}" <<EOF > /dev/null
# Modified to properly configure Bluetooth
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

if [[ "$FLAG_ANY_SYSTEM_PATCH" == true ]]; then
  echo "Unmounting development system.img.."
  sudo umount "${PATH_MOUNT}"

  # Flash via MTKClient. Resolve location + venv only now (no point checking
  # earlier — the patch steps don't need MTKClient).
  PATH_MTKCLIENT="$(resolve_mtkclient_dir)"
  PATH_VENV_MTKCLIENT="${PATH_MTKCLIENT}/venv"
  if [[ ! -f "${PATH_VENV_MTKCLIENT}/bin/activate" ]]; then
    echo "ERROR: MTKClient venv missing at ${PATH_VENV_MTKCLIENT}." >&2
    echo "       Run: ${PATH_SCRIPT_DIR}/tools/setup.sh" >&2
    exit 1
  fi

  echo "Activating MTKClient venv (${PATH_MTKCLIENT}).."
  cd "${PATH_MTKCLIENT}"
  # shellcheck disable=SC1091
  source "${PATH_VENV_MTKCLIENT}/bin/activate"

  echo "Writing new system.img (plug in and reset Y1 device using button near USB-C port).."
  python3 "${PATH_MTKCLIENT}/mtk.py" w android "${PATH_ARTIFACTS}/${FILENAME_SYSTEM_IMAGE_TARGET}"

  echo "Deactivating MTKClient venv.."
  deactivate
fi
echo "Done!"
