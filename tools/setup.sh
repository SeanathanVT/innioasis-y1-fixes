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

case "${1:-}" in
    -h|--help)
        cat <<EOF
Usage: ./tools/setup.sh

One-time tooling provisioner. Idempotent — re-runnable, skips work
already done. Clones MTKClient at the pinned ref into tools/mtkclient/
and creates two Python venvs (tools/mtkclient/venv/ for MTKClient deps,
tools/python-venv/ for patcher deps from python-requirements.txt).

To force a refresh of either subdir, delete it and re-run:
    rm -rf tools/mtkclient && ./tools/setup.sh
    rm -rf tools/python-venv && ./tools/setup.sh

To bump the MTKClient pin, change MTKCLIENT_REF below and re-run.
EOF
        exit 0
        ;;
esac

# Pinned MTKClient version. Last verified working: v2.1.4.1.
# Bump after verifying compatibility against this repo's flash flow.
# (List upstream tags: git ls-remote --tags https://github.com/bkerler/mtkclient.git)
MTKCLIENT_REPO="https://github.com/bkerler/mtkclient.git"
MTKCLIENT_REF="v2.1.4.1"

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
# Idempotent: clone if missing, always ensure HEAD is at MTKCLIENT_REF
# (so a re-run heals a partial state where the clone succeeded but the
# checkout failed, e.g. a wrong tag name on the previous run).

if [[ ! -d mtkclient ]]; then
    echo "[setup] Cloning MTKClient.."
    git clone --quiet "${MTKCLIENT_REPO}" mtkclient
elif [[ ! -d mtkclient/.git ]]; then
    echo "ERROR: tools/mtkclient/ exists but isn't a git checkout." >&2
    echo "       Remove it (rm -rf tools/mtkclient) and re-run." >&2
    exit 1
fi

# Ensure mtkclient is at MTKCLIENT_REF. git checkout to the current ref
# is a no-op; if the ref doesn't exist locally, fetch tags first.
if ! ( cd mtkclient && git checkout --quiet "${MTKCLIENT_REF}" 2>/dev/null ); then
    echo "[setup] Fetching tags from origin to find ${MTKCLIENT_REF}.."
    ( cd mtkclient && git fetch --quiet --tags )
    if ! ( cd mtkclient && git checkout --quiet "${MTKCLIENT_REF}" 2>/dev/null ); then
        echo "ERROR: ${MTKCLIENT_REF} not found in ${MTKCLIENT_REPO}." >&2
        echo "       List upstream tags: git ls-remote --tags ${MTKCLIENT_REPO}" >&2
        echo "       Update MTKCLIENT_REF at the top of $0." >&2
        exit 1
    fi
fi
echo "[setup] tools/mtkclient/ at ${MTKCLIENT_REF}."

# --- 2. mtkclient venv + deps ----------------------------------------------

if [[ -d mtkclient/venv ]]; then
    echo "[setup] tools/mtkclient/venv/ exists — skipping."
else
    echo "[setup] Creating tools/mtkclient/venv/.."
    python3 -m venv mtkclient/venv
    # shellcheck disable=SC1091
    source mtkclient/venv/bin/activate
    pip install --upgrade pip
    if [[ -f mtkclient/requirements.txt ]]; then
        pip install -r mtkclient/requirements.txt
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
    pip install --upgrade pip
    pip install -r python-requirements.txt
    deactivate
fi

echo "[setup] Done."
echo ""
echo "  tools/mtkclient/         (MTKClient ${MTKCLIENT_REF})"
echo "  tools/mtkclient/venv/    (MTKClient deps)"
echo "  tools/python-venv/       (patcher deps from python-requirements.txt)"
echo ""
echo "If you'll use --avrcp (builds Y1MediaBridge.apk via gradle), also run:"
echo "  ./tools/install-android-sdk.sh         # auto-installs Android SDK to tools/android-sdk/ (~1.5GB)"
echo "  source tools/android-sdk-env.sh        # then source the env file for adb/sdkmanager on PATH"
echo ""
echo "Or skip if you have an Android SDK elsewhere — see docs/ANDROID-SDK.md."
