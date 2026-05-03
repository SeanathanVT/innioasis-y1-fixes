#!/usr/bin/env bash
#
# tools/setup.sh — idempotent: clone MTKClient at the pinned ref, create
# Python venvs (mtkclient deps + patcher deps).
#
# Re-runnable. Skips work that's already done. To force a refresh, delete the
# subdir and re-run (e.g. `rm -rf tools/mtkclient && tools/setup.sh`).
#
# Bumping the MTKClient pin: change MTKCLIENT_REF below, delete tools/mtkclient,
# re-run.

set -euo pipefail

# Pinned MTKClient version. Last verified working: 2.1.4.1.
# Bump after verifying compatibility against this repo's flash flow.
MTKCLIENT_REPO="https://github.com/bkerler/mtkclient.git"
MTKCLIENT_REF="2.1.4.1"

cd "$(dirname "${BASH_SOURCE[0]}")"
TOOLS_DIR="$(pwd)"

# --- prereq checks ---------------------------------------------------------

for cmd in git python3 pip; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        if [[ "$cmd" == "pip" ]] && python3 -m pip --version >/dev/null 2>&1; then
            continue
        fi
        echo "ERROR: '$cmd' not found in PATH" >&2
        exit 1
    fi
done

if ! python3 -c "import venv" >/dev/null 2>&1; then
    echo "ERROR: python3 'venv' module unavailable. Install python3-venv (Debian/Ubuntu)" >&2
    echo "       or ensure your python3 build includes venv (Rocky/Fedora ship it by default)." >&2
    exit 1
fi

# --- 1. Clone MTKClient at the pinned ref ----------------------------------

if [[ -d mtkclient ]]; then
    echo "[setup] tools/mtkclient/ exists — skipping clone."
    echo "        To refresh: rm -rf tools/mtkclient && $0"
else
    echo "[setup] Cloning MTKClient at ${MTKCLIENT_REF}.."
    git clone "${MTKCLIENT_REPO}" mtkclient
    ( cd mtkclient && git checkout "${MTKCLIENT_REF}" )
fi

# --- 2. mtkclient venv + deps ----------------------------------------------

if [[ -d mtkclient/venv ]]; then
    echo "[setup] tools/mtkclient/venv/ exists — skipping."
else
    echo "[setup] Creating tools/mtkclient/venv/.."
    python3 -m venv mtkclient/venv
    # shellcheck disable=SC1091
    source mtkclient/venv/bin/activate
    pip install --quiet --upgrade pip
    if [[ -f mtkclient/requirements.txt ]]; then
        pip install --quiet -r mtkclient/requirements.txt
    else
        echo "WARNING: mtkclient/requirements.txt not found at this ref — venv created empty" >&2
    fi
    deactivate
fi

# --- 3. python-venv for patcher deps (androguard etc.) ---------------------

if [[ -d python-venv ]]; then
    echo "[setup] tools/python-venv/ exists — skipping."
else
    echo "[setup] Creating tools/python-venv/.."
    python3 -m venv python-venv
    # shellcheck disable=SC1091
    source python-venv/bin/activate
    pip install --quiet --upgrade pip
    pip install --quiet -r python-requirements.txt
    deactivate
fi

echo "[setup] Done."
echo ""
echo "  tools/mtkclient/         (MTKClient ${MTKCLIENT_REF})"
echo "  tools/mtkclient/venv/    (MTKClient deps)"
echo "  tools/python-venv/       (patcher deps from python-requirements.txt)"
echo ""
echo "If you'll use --avrcp (builds Y1MediaBridge.apk via gradle), also run:"
echo "  ./tools/install-android-sdk.sh    # auto-installs Android SDK to tools/android-sdk/ (~1.5GB)"
echo ""
echo "  After it finishes, source tools/android-sdk-env.sh in your shell"
echo "  if you want adb/sdkmanager on PATH (gradle itself doesn't need this)."
echo ""
echo "Or skip if you have an Android SDK elsewhere — see docs/ANDROID-SDK.md."
