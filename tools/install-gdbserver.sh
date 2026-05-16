#!/usr/bin/env bash
#
# install-gdbserver.sh — install an ARM 32-bit static gdbserver (API 17,
# Android 4.2.2) at tools/gdbserver. Sources in priority order:
# existing tools/gdbserver, NDK r10e–r17c prebuilt, AOSP prebuilts/misc.
# Prereqs: curl, file, sha256sum. Idempotent.

set -euo pipefail

case "${1:-}" in
    -h|--help)
        cat <<EOF
Usage: ./tools/install-gdbserver.sh

Auto-installer for an ARM 32-bit static gdbserver compatible with Android
4.2.2 (API 17). Installed at tools/gdbserver. Required only by
tools/attach-mtkbt-gdb.sh, which is itself a research probe — not part
of the patch flow.

Sources tried, in order:
  1. existing tools/gdbserver if already valid (short-circuits)
  2. \$ANDROID_NDK_HOME/prebuilt/android-arm/gdbserver/gdbserver
  3. AOSP prebuilts/misc via the github.com/aosp-mirror raw download

To force a fresh download: rm tools/gdbserver && ./tools/install-gdbserver.sh
To pin a specific upstream commit: edit AOSP_COMMIT below.

The downloaded binary is validated as:
  - ELF 32-bit, ARM, EABI5
  - statically linked
  - executable

If validation fails the partial download is removed.

Disk:    ~3 MB
Network: ~3 MB (skipped on cache hit)
Prereq:  curl, file, sha256sum
EOF
        exit 0
        ;;
esac

# Pinning. Source: github.com/aosp-mirror/platform_prebuilt — the canonical
# Google-published mirror of AOSP's prebuilts tree. The android-arm/gdbserver
# binary hasn't been touched upstream since 2010-12-07, so pinning to the
# last-touch commit gives full reproducibility. To bump: change AOSP_COMMIT
# + EXPECTED_SHA256 in tandem.
AOSP_COMMIT="f5033a8c79b8934b35f5efa1bc2a0b3231e7f24d"
AOSP_URL="https://raw.githubusercontent.com/aosp-mirror/platform_prebuilt/${AOSP_COMMIT}/android-arm/gdbserver/gdbserver"
EXPECTED_SHA256="1c3db6a3e37bb4d9b2ada30111d9f7b19f735020f163670e5bf7cc9beb558fd4"
EXPECTED_BYTES=186112

cd "$(dirname "${BASH_SOURCE[0]}")"
TOOLS_DIR="$(pwd)"
REPO_ROOT="$(cd .. && pwd)"
TARGET="${TOOLS_DIR}/gdbserver"

# --- helpers --------------------------------------------------------------

# validate <path>: returns 0 if path exists and is a usable ARM static gdbserver.
validate() {
    local path="$1"
    [[ -f "$path" && -x "$path" ]] || return 1
    local info
    info=$(file -b "$path" 2>/dev/null) || return 1
    case "$info" in
        *ARM*statically\ linked*) return 0 ;;
        *) return 1 ;;
    esac
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

# --- short-circuit on already-valid install -------------------------------

if validate "$TARGET"; then
    sha=$(sha256_of "$TARGET")
    bytes=$(wc -c < "$TARGET")
    echo "[install-gdbserver] tools/gdbserver already valid (${bytes} bytes, sha256 ${sha:0:16}…)."
    echo "                    Re-run after 'rm tools/gdbserver' to force a fresh fetch."
    exit 0
fi

# --- try copying from an installed NDK ------------------------------------

NDK_PATHS=(
    "${ANDROID_NDK_HOME:-/dev/null}/prebuilt/android-arm/gdbserver/gdbserver"
    "${ANDROID_NDK_ROOT:-/dev/null}/prebuilt/android-arm/gdbserver/gdbserver"
)
for ndk_path in "${NDK_PATHS[@]}"; do
    if validate "$ndk_path"; then
        echo "[install-gdbserver] Copying from NDK at ${ndk_path}"
        cp "$ndk_path" "$TARGET"
        chmod 755 "$TARGET"
        if validate "$TARGET"; then
            sha=$(sha256_of "$TARGET")
            echo "[install-gdbserver] Installed at ${TARGET}"
            echo "                    sha256: ${sha}"
            exit 0
        fi
        rm -f "$TARGET"
        echo "                    NDK copy failed validation; trying download." >&2
        break
    fi
done

# --- download from AOSP prebuilts mirror ----------------------------------

for cmd in curl file; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "ERROR: '$cmd' not in PATH" >&2
        exit 1
    }
done
if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
    echo "ERROR: neither sha256sum nor shasum available" >&2
    exit 1
fi

echo "[install-gdbserver] Downloading from AOSP prebuilts mirror.."
echo "                    ${AOSP_URL}"

# Atomic-ish: download to a tmp path, validate, then rename. Avoids leaving
# a half-fetched binary at tools/gdbserver if curl fails partway.
TMP="${TARGET}.partial"
trap 'rm -f "$TMP"' EXIT INT TERM

if ! curl -L --fail --silent --show-error -o "$TMP" "$AOSP_URL"; then
    cat >&2 <<EOF
ERROR: download failed.

The AOSP mirror URL is:
  ${AOSP_URL}

Possible causes:
  - No network access
  - github.com/aosp-mirror/platform_prebuilts_misc moved or removed the file
  - The pinned AOSP_COMMIT is no longer valid

Workarounds:
  - Fetch any ARM 32-bit static gdbserver compatible with API 17
    (NDK r10e through r17c all ship one at prebuilt/android-arm/gdbserver/),
    place it at tools/gdbserver, and re-run.
  - Override AOSP_COMMIT at the top of this script and re-run.
EOF
    exit 1
fi

bytes=$(wc -c < "$TMP")
sha=$(sha256_of "$TMP")
echo "[install-gdbserver] Downloaded ${bytes} bytes, sha256 ${sha}"

if [ "$bytes" != "$EXPECTED_BYTES" ]; then
    echo "ERROR: downloaded ${bytes} bytes, expected ${EXPECTED_BYTES}." >&2
    echo "       The pinned upstream file may have changed; verify and update" >&2
    echo "       AOSP_COMMIT + EXPECTED_SHA256 + EXPECTED_BYTES in this script." >&2
    exit 1
fi
if [ "$sha" != "$EXPECTED_SHA256" ]; then
    echo "ERROR: sha256 mismatch — refusing to install." >&2
    echo "       got:      ${sha}" >&2
    echo "       expected: ${EXPECTED_SHA256}" >&2
    exit 1
fi

chmod 755 "$TMP"

if ! validate "$TMP"; then
    info=$(file -b "$TMP" 2>/dev/null || echo "(file failed)")
    cat >&2 <<EOF
ERROR: downloaded file is not a valid ARM static binary.
       file says: ${info}

This usually means:
  - GitHub returned an HTML error page instead of the binary (404 etc.)
  - The mirror restructured and the file is at a different path now

The partial download has been removed. Try:
  - Verify the URL above is reachable from a browser
  - Pin AOSP_COMMIT to a known-good commit hash
EOF
    exit 1
fi

mv "$TMP" "$TARGET"
trap - EXIT INT TERM

echo "[install-gdbserver] Installed at ${TARGET}"
echo "                    sha256: ${sha}"
echo "                    Verify on-device: tools/attach-mtkbt-gdb.sh will push"
echo "                    this to /data/local/tmp/gdbserver under su."
