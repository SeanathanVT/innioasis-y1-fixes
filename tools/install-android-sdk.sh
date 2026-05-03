#!/usr/bin/env bash
#
# tools/install-android-sdk.sh — auto-install Android cmdline-tools and the
# components needed to build src/Y1MediaBridge/ via gradle. Skipped if a
# usable SDK is already present (ANDROID_HOME or tools/android-sdk/).
#
# Wires the result into src/Y1MediaBridge/local.properties via sdk.dir=,
# so gradle finds the SDK without needing ANDROID_HOME in your shell.
#
# Prereqs:  JDK 17+, curl, unzip
# Disk:     ~1.5–2 GB
# Network:  ~1.7 GB total
#
# By running this script you implicitly accept Google's Android SDK license
# — it gets piped "yes" for every component.

set -euo pipefail

# Pinned commandline-tools build. To bump: change CMDLINE_TOOLS_BUILD,
# delete tools/android-sdk, re-run. The build number is the only changing
# piece in the Google CDN URL; latest list at developer.android.com.
CMDLINE_TOOLS_BUILD="11076708"

# Component versions to install (bump alongside src/Y1MediaBridge/app/build.gradle).
ANDROID_PLATFORM="android-34"
BUILD_TOOLS_VERSION="34.0.0"

cd "$(dirname "${BASH_SOURCE[0]}")"
TOOLS_DIR="$(pwd)"
SDK_DIR="${TOOLS_DIR}/android-sdk"
REPO_ROOT="$(cd .. && pwd)"
LOCAL_PROPS="${REPO_ROOT}/src/Y1MediaBridge/local.properties"

# --- OS detection ---------------------------------------------------------

case "$(uname -s)" in
    Linux*)  OS=linux ;;
    Darwin*) OS=mac ;;
    *)
        cat >&2 <<EOF
ERROR: Auto-install supports Linux and macOS only.
       Windows users: see docs/ANDROID-SDK.md for manual steps.
EOF
        exit 1
        ;;
esac

# --- Already-installed short-circuit -------------------------------------

if [[ -n "${ANDROID_HOME:-}" && -d "${ANDROID_HOME}/platforms/${ANDROID_PLATFORM}" ]]; then
    echo "[install-sdk] ANDROID_HOME=${ANDROID_HOME} already valid (has platforms/${ANDROID_PLATFORM}/) — skipping."
    exit 0
fi

if [[ -d "${SDK_DIR}/platforms/${ANDROID_PLATFORM}" ]]; then
    echo "[install-sdk] tools/android-sdk/ already populated — skipping."
    echo "              To force refresh: rm -rf tools/android-sdk && $0"
    exit 0
fi

# --- Prereq checks --------------------------------------------------------

for cmd in curl unzip; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: '$cmd' not in PATH" >&2; exit 1; }
done

if ! command -v java >/dev/null 2>&1; then
    cat >&2 <<EOF
ERROR: java not found in PATH. JDK 17+ required by sdkmanager and AGP 8.x.
Install:
  Rocky / Alma / RHEL / Fedora: sudo dnf install -y java-17-openjdk-devel
  Debian / Ubuntu:              sudo apt install -y openjdk-17-jdk
  Arch:                         sudo pacman -S jdk17-openjdk
  macOS (Homebrew):             brew install --cask temurin
EOF
    exit 1
fi

JAVA_MAJOR=$(java -version 2>&1 | head -n1 | sed -E 's/.*"([0-9]+)\..*/\1/')
if [[ -z "${JAVA_MAJOR}" || "${JAVA_MAJOR}" -lt 17 ]]; then
    echo "ERROR: java major version ${JAVA_MAJOR:-unknown} detected; need 17+." >&2
    echo "       Set JAVA_HOME to a JDK 17+ install or upgrade your default JDK." >&2
    exit 1
fi

# --- Download + unpack cmdline-tools --------------------------------------

ZIP_URL="https://dl.google.com/android/repository/commandlinetools-${OS}-${CMDLINE_TOOLS_BUILD}_latest.zip"
ZIP_FILE="${TOOLS_DIR}/.cmdline-tools-${OS}-${CMDLINE_TOOLS_BUILD}.zip"

echo "[install-sdk] Downloading cmdline-tools build ${CMDLINE_TOOLS_BUILD} (~150MB).."
echo "              ${ZIP_URL}"
curl -L --fail -o "${ZIP_FILE}" "${ZIP_URL}"

if command -v sha256sum >/dev/null 2>&1; then
    ZIP_SHA256=$(sha256sum "${ZIP_FILE}" | awk '{print $1}')
else
    ZIP_SHA256=$(shasum -a 256 "${ZIP_FILE}" | awk '{print $1}')
fi
echo "[install-sdk] Downloaded sha256: ${ZIP_SHA256}"
echo "              (Compare against developer.android.com/studio#command-tools if you want"
echo "               supply-chain verification before proceeding.)"

echo "[install-sdk] Unpacking to ${SDK_DIR}/cmdline-tools/latest/.."
mkdir -p "${SDK_DIR}/cmdline-tools"
( cd "${SDK_DIR}/cmdline-tools" && unzip -q "${ZIP_FILE}" )

# Google's zip extracts to cmdline-tools/cmdline-tools/, but sdkmanager
# expects cmdline-tools/latest/. Rename the inner dir.
mv "${SDK_DIR}/cmdline-tools/cmdline-tools" "${SDK_DIR}/cmdline-tools/latest"
rm "${ZIP_FILE}"

SDKMANAGER="${SDK_DIR}/cmdline-tools/latest/bin/sdkmanager"
[[ -x "${SDKMANAGER}" ]] || { echo "ERROR: sdkmanager not where expected at ${SDKMANAGER}" >&2; exit 1; }

# --- Accept licenses + install components ---------------------------------

echo "[install-sdk] Accepting Google's Android SDK licenses (yes | sdkmanager --licenses).."
yes | "${SDKMANAGER}" --sdk_root="${SDK_DIR}" --licenses >/dev/null

echo "[install-sdk] Installing platforms;${ANDROID_PLATFORM}, build-tools;${BUILD_TOOLS_VERSION}, platform-tools (~1.5GB).."
"${SDKMANAGER}" --sdk_root="${SDK_DIR}" --install \
    "platforms;${ANDROID_PLATFORM}" \
    "build-tools;${BUILD_TOOLS_VERSION}" \
    "platform-tools"

# --- Wire into local.properties so gradle finds the SDK -------------------

if [[ -f "${LOCAL_PROPS}" ]] && grep -q "^sdk\.dir=" "${LOCAL_PROPS}" && \
   ! grep -q "^sdk\.dir=${SDK_DIR}\$" "${LOCAL_PROPS}"; then
    EXISTING=$(grep "^sdk\.dir=" "${LOCAL_PROPS}" | head -n1 | cut -d= -f2-)
    cat >&2 <<EOM
WARNING: ${LOCAL_PROPS} already has sdk.dir=${EXISTING}
         Not overwriting (you set this manually).
         Edit it yourself if you want gradle to use the auto-installed SDK at:
           ${SDK_DIR}
EOM
else
    echo "sdk.dir=${SDK_DIR}" > "${LOCAL_PROPS}"
    echo "[install-sdk] Wrote sdk.dir=${SDK_DIR} → ${LOCAL_PROPS}"
fi

cat <<EOF

[install-sdk] Done.

  SDK at:           ${SDK_DIR}
  Components:       platforms;${ANDROID_PLATFORM}, build-tools;${BUILD_TOOLS_VERSION}, platform-tools
  Local props:      ${LOCAL_PROPS}

src/Y1MediaBridge/gradlew assembleDebug should now succeed without needing
ANDROID_HOME in your shell — gradle reads sdk.dir from local.properties.
EOF
