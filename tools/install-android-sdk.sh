#!/usr/bin/env bash
#
# install-android-sdk.sh — install Android cmdline-tools and components
# needed by src/Y1Bridge/. Idempotent. Writes src/Y1Bridge/local.properties
# (sdk.dir=…) and tools/android-sdk-env.sh. Prereqs: JDK 17+, curl, unzip.
# Disk ~1.5-2 GB, network ~1.7 GB. Pipes "yes" to the Android SDK license.

set -euo pipefail

case "${1:-}" in
    -h|--help)
        cat <<EOF
Usage: ./tools/install-android-sdk.sh

Auto-installer for the Android SDK on Linux. Required for building
src/Y1Bridge/ via Gradle; needed only for the --avrcp flag
(which is currently known-broken — see INVESTIGATION.md).

Detects existing \$ANDROID_HOME and short-circuits; otherwise
downloads Google's pinned commandline-tools archive into
tools/android-sdk/, accepts licenses, and installs platforms;android-34
+ build-tools;34.0.0 + platform-tools.

Always writes:
  - src/Y1Bridge/local.properties (sdk.dir=…) — Gradle reads this
  - tools/android-sdk-env.sh — sourceable for ANDROID_HOME on PATH

Disk:    ~1.5–2 GB
Network: ~1.7 GB (skipped if cmdline-tools already present)
Prereq:  JDK 17+, curl, unzip

To force a fresh download: rm -rf tools/android-sdk && ./tools/install-android-sdk.sh
To bump the cmdline-tools pin: change CMDLINE_TOOLS_BUILD below and re-run.

By running this script you implicitly accept Google's Android SDK
licenses — it gets piped "yes" for every component.
EOF
        exit 0
        ;;
esac

# Pinned commandline-tools build. To bump: change CMDLINE_TOOLS_BUILD,
# delete tools/android-sdk, re-run.
CMDLINE_TOOLS_BUILD="14742923"

# Component versions to install (bump alongside src/Y1Bridge/app/build.gradle).
ANDROID_PLATFORM="android-34"
BUILD_TOOLS_VERSION="34.0.0"

cd "$(dirname "${BASH_SOURCE[0]}")"
TOOLS_DIR="$(pwd)"
SDK_DIR="${TOOLS_DIR}/android-sdk"
REPO_ROOT="$(cd .. && pwd)"
LOCAL_PROPS="${REPO_ROOT}/src/Y1Bridge/local.properties"
ENV_FILE="${TOOLS_DIR}/android-sdk-env.sh"

# --- OS detection ---------------------------------------------------------
# Linux only. The whole project (apply.bash mount/flash path) is Linux-only,
# so the SDK installer doesn't bother with macOS / other-Unix variants.

case "$(uname -s)" in
    Linux*)  OS=linux ;;
    *)
        cat >&2 <<EOF
ERROR: This script (and the rest of the project's flash flow) is Linux-only.
       The patcher uses mount -o loop and GNU sed -i, which don't work on
       macOS / BSD / Windows. Run from a Linux host or Linux VM.
EOF
        exit 1
        ;;
esac

# sdk_complete: returns 0 iff <root> has platforms/, build-tools/, and
# platform-tools/ — gating on all three so partial-install failures don't
# silently short-circuit on the next run.
sdk_complete() {
    local root="$1"
    [[ -d "${root}/platforms/${ANDROID_PLATFORM}" ]] && \
    [[ -d "${root}/build-tools/${BUILD_TOOLS_VERSION}" ]] && \
    [[ -d "${root}/platform-tools" ]]
}

NEED_INSTALL=true

if [[ -n "${ANDROID_HOME:-}" ]] && sdk_complete "${ANDROID_HOME}"; then
    echo "[install-sdk] Reusing ANDROID_HOME=${ANDROID_HOME} (has all required components)."
    SDK_TARGET="${ANDROID_HOME}"
    NEED_INSTALL=false
elif sdk_complete "${SDK_DIR}"; then
    echo "[install-sdk] Reusing existing tools/android-sdk/ (all required components present)."
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
ERROR: java not found in PATH. JDK 17+ required by sdkmanager and AGP 9.x.
Install:
  Rocky / Alma / RHEL / Fedora: sudo dnf install -y java-17-openjdk-devel
  Debian / Ubuntu:              sudo apt install -y openjdk-17-jdk
  Arch:                         sudo pacman -S jdk17-openjdk
EOF
        exit 1
    fi

    JAVA_MAJOR=$(java -version 2>&1 | head -n1 | sed -E 's/.*"([0-9]+)[^0-9].*/\1/;s/.*"([0-9]+)".*/\1/')
    if [[ -z "${JAVA_MAJOR}" || "${JAVA_MAJOR}" -lt 17 ]]; then
        echo "ERROR: java major version ${JAVA_MAJOR:-unknown} detected; need 17+." >&2
        echo "       Set JAVA_HOME to a JDK 17+ install or upgrade your default JDK." >&2
        exit 1
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

  Gradle build (cd src/Y1Bridge && ./gradlew --stop && ./gradlew
  assembleDebug) reads sdk.dir from local.properties directly — no shell
  setup needed for that.

╔══════════════════════════════════════════════════════════════════════════╗
║  NEXT STEP — source the env file in your current shell:                  ║
║                                                                          ║
║      source tools/android-sdk-env.sh                                     ║
║                                                                          ║
║  Adds ANDROID_HOME, adb, and sdkmanager to your PATH for this session.   ║
║  Persist across shells by appending the same line to ~/.bashrc /         ║
║  ~/.zshrc / your shell's rc file.                                        ║
╚══════════════════════════════════════════════════════════════════════════╝
EOF
