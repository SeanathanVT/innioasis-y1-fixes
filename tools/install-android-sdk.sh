#!/usr/bin/env bash
#
# tools/install-android-sdk.sh — auto-install Android cmdline-tools and the
# components needed to build src/Y1MediaBridge/ via gradle. Re-runnable;
# safe to run after a partial first run (still writes local.properties +
# env file at the end so any prior partial state is healed).
#
# Wires the result into:
#   - src/Y1MediaBridge/local.properties (sdk.dir=…) — gradle reads this
#     directly; gradle builds work without ANDROID_HOME in your shell.
#   - tools/android-sdk-env.sh — sourceable by the user; exports
#     ANDROID_HOME and adds adb / sdkmanager to PATH for interactive use.
#
# Prereqs:  JDK 17+, curl, unzip
# Disk:     ~1.5–2 GB
# Network:  ~1.7 GB total
#
# By running this script you implicitly accept Google's Android SDK license
# — it gets piped "yes" for every component.

set -euo pipefail

# Pinned commandline-tools build. To bump: change CMDLINE_TOOLS_BUILD,
# delete tools/android-sdk, re-run.
CMDLINE_TOOLS_BUILD="11076708"

# Component versions to install (bump alongside src/Y1MediaBridge/app/build.gradle).
ANDROID_PLATFORM="android-34"
BUILD_TOOLS_VERSION="34.0.0"

cd "$(dirname "${BASH_SOURCE[0]}")"
TOOLS_DIR="$(pwd)"
SDK_DIR="${TOOLS_DIR}/android-sdk"
REPO_ROOT="$(cd .. && pwd)"
LOCAL_PROPS="${REPO_ROOT}/src/Y1MediaBridge/local.properties"
ENV_FILE="${TOOLS_DIR}/android-sdk-env.sh"

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

# --- Decide which SDK to wire up -----------------------------------------
# Three cases. In every case the local.properties + env-file write at the
# end always runs — re-running this script heals missing config files.

NEED_INSTALL=true

if [[ -n "${ANDROID_HOME:-}" && -d "${ANDROID_HOME}/platforms/${ANDROID_PLATFORM}" ]]; then
    echo "[install-sdk] Reusing ANDROID_HOME=${ANDROID_HOME} (has platforms/${ANDROID_PLATFORM}/)."
    SDK_TARGET="${ANDROID_HOME}"
    NEED_INSTALL=false
elif [[ -d "${SDK_DIR}/platforms/${ANDROID_PLATFORM}" ]]; then
    echo "[install-sdk] Reusing existing tools/android-sdk/ (platforms/${ANDROID_PLATFORM}/ already present)."
    echo "              To force a fresh download: rm -rf ${SDK_DIR} && $0"
    SDK_TARGET="${SDK_DIR}"
    NEED_INSTALL=false
else
    SDK_TARGET="${SDK_DIR}"
fi

# --- Install (only if we don't already have a usable SDK) -----------------

if [[ "${NEED_INSTALL}" == "true" ]]; then
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

    JAVA_MAJOR=$(java -version 2>&1 | head -n1 | sed -E 's/.*"([0-9]+)[^0-9].*/\1/;s/.*"([0-9]+)".*/\1/')
    if [[ -z "${JAVA_MAJOR}" || "${JAVA_MAJOR}" -lt 17 ]]; then
        echo "ERROR: java major version ${JAVA_MAJOR:-unknown} detected; need 17+." >&2
        echo "       Set JAVA_HOME to a JDK 17+ install or upgrade your default JDK." >&2
        exit 1
    fi
    if [[ "${JAVA_MAJOR}" -gt 21 ]]; then
        cat >&2 <<EOM
WARNING: JDK ${JAVA_MAJOR} detected. AGP 8.7.3 (used by src/Y1MediaBridge/) supports
         JDK 17–21; JDK ${JAVA_MAJOR} is likely to fail at build time with
         "Toolchain ... does not provide the required capabilities: [JAVA_COMPILER]"
         or similar. Install JDK 17 and set JAVA_HOME before running gradle:
           sudo dnf install -y java-17-openjdk-devel        # Rocky/Alma/RHEL/Fedora
           sudo apt install -y openjdk-17-jdk               # Debian/Ubuntu
           export JAVA_HOME=/usr/lib/jvm/java-17-openjdk    # adjust per distro
         (sdkmanager itself works fine on JDK ${JAVA_MAJOR}; this only
         affects \`./gradlew assembleDebug\` later.)

EOM
    fi

    # --- Download + unpack cmdline-tools ----------------------------------
    # Skip the download+unpack if a prior run already produced a working
    # sdkmanager — only the component install + license-accept will retry.

    SDKMANAGER="${SDK_DIR}/cmdline-tools/latest/bin/sdkmanager"

    if [[ -x "${SDKMANAGER}" ]]; then
        echo "[install-sdk] cmdline-tools/latest/bin/sdkmanager already present — skipping download."
    else
        # Wipe any half-extracted state from a prior failed run.
        rm -rf "${SDK_DIR}/cmdline-tools"

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

        echo "[install-sdk] Unpacking to ${SDK_DIR}/cmdline-tools/latest/.."
        mkdir -p "${SDK_DIR}/cmdline-tools"
        ( cd "${SDK_DIR}/cmdline-tools" && unzip -q -o "${ZIP_FILE}" )

        # Google's zip extracts to cmdline-tools/cmdline-tools/, but sdkmanager
        # expects cmdline-tools/latest/. Rename the inner dir.
        mv "${SDK_DIR}/cmdline-tools/cmdline-tools" "${SDK_DIR}/cmdline-tools/latest"
        rm "${ZIP_FILE}"

        [[ -x "${SDKMANAGER}" ]] || { echo "ERROR: sdkmanager not where expected at ${SDKMANAGER}" >&2; exit 1; }
    fi

    # --- Accept licenses (with explicit error visibility) -----------------
    # No stdout redirect: any sdkmanager error is visible immediately.
    # Wrapped so a non-zero exit prints a useful manual-debug pointer
    # instead of silently aborting via set -e.

    # Feed 'yes' via process substitution rather than a pipe. With \`yes |
    # sdkmanager\`, when sdkmanager finishes and closes its stdin, yes gets
    # SIGPIPE and exits 141; \`set -o pipefail\` then reports the pipe as
    # failed even when sdkmanager itself succeeded. < <(yes) avoids the
    # pipefail accounting because process substitution runs yes in a
    # background subshell whose exit isn't part of the foreground command.
    echo "[install-sdk] Accepting Google's Android SDK licenses.."
    if ! "${SDKMANAGER}" --sdk_root="${SDK_DIR}" --licenses < <(yes); then
        cat >&2 <<EOM
ERROR: sdkmanager --licenses failed.
       Run manually with full output to see why:
         "${SDKMANAGER}" --sdk_root="${SDK_DIR}" --licenses
       Common causes: JDK <17 picked up via JAVA_HOME, or no network.
       Current java: $(java -version 2>&1 | head -n1)
EOM
        exit 1
    fi

    echo "[install-sdk] Installing platforms;${ANDROID_PLATFORM}, build-tools;${BUILD_TOOLS_VERSION}, platform-tools (~1.5GB).."
    if ! "${SDKMANAGER}" --sdk_root="${SDK_DIR}" --install \
        "platforms;${ANDROID_PLATFORM}" \
        "build-tools;${BUILD_TOOLS_VERSION}" \
        "platform-tools"; then
        echo "ERROR: sdkmanager --install failed (see output above)." >&2
        exit 1
    fi
fi

# --- Wire SDK_TARGET into local.properties (always) -----------------------
# Always overwrite. local.properties is per-machine and gitignored, so
# we're not clobbering anything tracked. Re-running this script means the
# user wants the wiring refreshed.

echo "sdk.dir=${SDK_TARGET}" > "${LOCAL_PROPS}"
echo "[install-sdk] Wrote sdk.dir=${SDK_TARGET} → ${LOCAL_PROPS}"

# --- Write tools/android-sdk-env.sh (always) ------------------------------
# Distinct from local.properties: that file is for gradle's build-time
# SDK lookup; this file is sourceable by the user's shell to get
# ANDROID_HOME + adb/sdkmanager on PATH for interactive use.

cat > "${ENV_FILE}" <<EOF
# tools/android-sdk-env.sh — auto-generated by install-android-sdk.sh.
# Source this to get ANDROID_HOME + adb/sdkmanager on PATH:
#     source tools/android-sdk-env.sh
# Re-generated on every run of install-android-sdk.sh; do not hand-edit.
export ANDROID_HOME="${SDK_TARGET}"
export PATH="\$PATH:\$ANDROID_HOME/cmdline-tools/latest/bin:\$ANDROID_HOME/platform-tools"
EOF
echo "[install-sdk] Wrote ${ENV_FILE}"

# --- Summary --------------------------------------------------------------

cat <<EOF

[install-sdk] Done.

  SDK at:        ${SDK_TARGET}
  Components:    platforms;${ANDROID_PLATFORM}, build-tools;${BUILD_TOOLS_VERSION}, platform-tools
  Local props:   ${LOCAL_PROPS}     (gradle reads sdk.dir from here)
  Env file:      ${ENV_FILE}        (source for ANDROID_HOME + adb/sdkmanager on PATH)

  src/Y1MediaBridge/gradlew assembleDebug should now succeed.

  For shell tools (adb, sdkmanager) on PATH:
      source tools/android-sdk-env.sh
  Or persist by appending the same line to your ~/.bashrc / ~/.zshrc.
EOF
