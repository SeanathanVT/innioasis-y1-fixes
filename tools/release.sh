#!/usr/bin/env bash
# release.sh — bump the bash's # Version: header, fold [Unreleased] CHANGELOG
# entries into a new version section, commit, and tag.
#
# What it does (in order):
#   1. Validate args: $1 is a semver (X.Y.Z); --push is optional.
#   2. Check the working tree is clean (+ git config user.name/.email set,
#      + python3 available — both used later).
#   3. Check no tag named v$VERSION already exists.
#   4. Check CHANGELOG.md has a [Unreleased] section with at least one bullet
#      under it (refuse to release an empty CHANGELOG entry).
#   5. Verify apply.bash's `# Version:` header matches the expected regex
#      (so step 7a's sed doesn't fail mid-flight).
#   6. Rename `## [Unreleased]` to `## [$VERSION] - YYYY-MM-DD` in CHANGELOG.md
#      and prepend a fresh empty `## [Unreleased]` block above it. (This step
#      is the more-likely-to-fail of the two mutations, so it runs first —
#      if it fails, apply.bash hasn't been touched yet.)
#   7a. Update the bash's `# Version:` header in-place.
#   7b. Commit both files with message "Release v$VERSION" + the new CHANGELOG section body.
#   8. Create annotated tag `v$VERSION` at HEAD with the same message.
#   9. If --push given: push the commit and the tag together. Otherwise print
#      the commands you'd run to push later.
#
# Exits non-zero on any precondition failure; never partial-applies.
#
# Usage:
#   ./tools/release.sh 2.0.0
#   ./tools/release.sh 2.0.0 --push
#
# After:
#   git log --oneline --decorate=short --simplify-by-decoration | head
#   # → shows v2.0.0 alongside the rest of the version anchors

set -euo pipefail

case "${1:-}" in
    -h|--help)
        cat <<EOF
Usage: ./tools/release.sh <semver> [--push]

Release helper. Bumps apply.bash's # Version: header, renames
[Unreleased] in CHANGELOG.md to [<version>] - YYYY-MM-DD (and prepends
a new empty [Unreleased] above), commits both, and creates an
annotated v<version> tag at HEAD.

Refuses to proceed unless: <semver> is strict X.Y.Z (no v prefix,
no pre-release / build metadata), the working tree is clean,
no tag named v<semver> already exists, and [Unreleased] has at least
one bulleted entry.

After printing a summary, sleeps 3 seconds before mutating anything
(Ctrl-C to abort during that window).

Examples:
    ./tools/release.sh 2.0.0          # commits + tags locally
    ./tools/release.sh 2.0.0 --push   # also pushes current branch + tag

Without --push, prints the manual push commands at the end.
EOF
        exit 0
        ;;
esac

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    echo "usage: $0 <semver> [--push]" >&2
    echo "  e.g. $0 2.0.0" >&2
    echo "  e.g. $0 2.0.0 --push" >&2
    echo "  see: $0 --help" >&2
    exit 1
fi

VERSION="$1"
PUSH=false
if [ "${2:-}" = "--push" ]; then
    PUSH=true
elif [ -n "${2:-}" ]; then
    echo "ERROR: unknown second arg '$2' (expected --push or nothing)" >&2
    exit 1
fi

# Strict semver — match the existing tag scheme. Reject 'v' prefix in the arg
# (we add it for the tag) and reject pre-release / build metadata.
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: '$VERSION' is not a strict X.Y.Z semver" >&2
    echo "       (no 'v' prefix, no pre-release, no build metadata)" >&2
    exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

BASH_FILE="apply.bash"
CHANGELOG="CHANGELOG.md"
TAG="v$VERSION"
TODAY="$(date +%Y-%m-%d)"

# 2 — clean working tree
if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: working tree has uncommitted changes; aborting." >&2
    git status --short >&2
    exit 1
fi

# 2b — git author identity available (otherwise `git commit` fails late)
if [ -z "$(git config user.name)" ] || [ -z "$(git config user.email)" ]; then
    echo "ERROR: git user.name and/or user.email not set." >&2
    echo "       git commit will fail later. Set them first:" >&2
    echo "         git config --global user.name 'Your Name'" >&2
    echo "         git config --global user.email 'you@example.com'" >&2
    exit 1
fi

# 2c — python3 available (used to rewrite the CHANGELOG; without this check,
# the script would mutate apply.bash's # Version: header and then fail at
# the python3 invocation, leaving a half-applied state in violation of the
# "never partial-applies" promise above).
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not in PATH (used to rewrite CHANGELOG.md)." >&2
    exit 1
fi

# 3 — tag doesn't already exist
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    echo "ERROR: tag '$TAG' already exists." >&2
    git show -s --format="  → %h %s%n     %ad" --date=short "$TAG" >&2
    exit 1
fi

# 4 — CHANGELOG has a non-empty [Unreleased]
if ! grep -q "^## \[Unreleased\]" "$CHANGELOG"; then
    echo "ERROR: CHANGELOG.md has no '## [Unreleased]' section." >&2
    exit 1
fi
unreleased_body="$(awk '/^## \[Unreleased\]/{f=1; next} /^## \[/{f=0} f' "$CHANGELOG" | grep -E '^[-*]' || true)"
if [ -z "$unreleased_body" ]; then
    echo "ERROR: '## [Unreleased]' has no bulleted entries." >&2
    echo "       Add what's shipping in $VERSION before releasing." >&2
    exit 1
fi

# Show what's about to ship. This script is non-interactive by design —
# all preconditions have been validated above (clean tree, no existing tag,
# non-empty [Unreleased]). To abort: Ctrl-C during the 3-second pause below.
echo "About to release $TAG."
echo
echo "[Unreleased] entries that will become [$VERSION] - $TODAY:"
echo "---"
awk '/^## \[Unreleased\]/{f=1; next} /^## \[/{f=0} f' "$CHANGELOG" | sed 's/^/  /'
echo "---"
echo
echo "Files to be modified:"
echo "  - $BASH_FILE  (# Version: bump)"
echo "  - $CHANGELOG  (rename [Unreleased] → [$VERSION] - $TODAY, add new empty [Unreleased])"
echo "Then: commit + annotated tag $TAG."
if $PUSH; then
    echo "Then: git push origin $(git symbolic-ref --short HEAD) && git push origin $TAG."
else
    echo "(push: not requested; will print commands for later)"
fi
echo
echo "Proceeding in 3 seconds (Ctrl-C to abort)..."
sleep 3
echo

# 5 — verify the bash's # Version: header has the expected format BEFORE
# mutating CHANGELOG. This used to mutate apply.bash first and then run the
# Python rewrite — if the Python regex missed (malformed [Unreleased] line),
# the script bailed with apply.bash already mutated and CHANGELOG not, in
# violation of the "never partial-applies" promise.
if ! grep -qE '^# Version:[[:space:]]+[0-9]+\.[0-9]+\.[0-9]+[[:space:]]*$' "$BASH_FILE"; then
    echo "ERROR: $BASH_FILE has no '# Version:' header in the expected format." >&2
    exit 1
fi

# 6 — rewrite CHANGELOG first (the more-likely-to-fail step). If the regex
# misses or anything else goes wrong, the apply.bash sed below never runs.
# Also captures the [Unreleased] body for re-use as the commit + tag message.
RELEASE_BODY="$(mktemp)"
trap 'rm -f "$RELEASE_BODY"' EXIT
python3 - "$CHANGELOG" "$VERSION" "$TODAY" "$RELEASE_BODY" <<'PYEOF'
import sys, re, pathlib
path, version, today, body_path = sys.argv[1:5]
text = pathlib.Path(path).read_text()
m = re.search(r'^## \[Unreleased\]\s*\n(.*?)(?=^## \[|\Z)', text, re.M | re.S)
if not m:
    sys.exit("ERROR: failed to locate [Unreleased] section in CHANGELOG")
pathlib.Path(body_path).write_text(m.group(1).strip() + '\n')
new = re.sub(
    r'^## \[Unreleased\]\s*\n',
    f'## [Unreleased]\n\n## [{version}] - {today}\n',
    text,
    count=1,
    flags=re.M,
)
if new == text:
    sys.exit("ERROR: failed to rewrite [Unreleased] section in CHANGELOG")
pathlib.Path(path).write_text(new)
PYEOF
echo "[updated] $CHANGELOG  [Unreleased] → [$VERSION] - $TODAY (and new empty [Unreleased])"

# 7a — bump the bash's # Version: header (now safe — CHANGELOG is already written)
sed -i.bak -E "s/^# Version:[[:space:]]+[0-9]+\\.[0-9]+\\.[0-9]+[[:space:]]*$/# Version:   $VERSION/" "$BASH_FILE"
rm -f "$BASH_FILE.bak"
if ! grep -qE "^# Version:[[:space:]]+$VERSION[[:space:]]*$" "$BASH_FILE"; then
    echo "ERROR: bash version bump didn't take. Check $BASH_FILE." >&2
    exit 1
fi
echo "[bumped] $BASH_FILE  '# Version: $VERSION'"

# 7b — commit. Message = "Release v$VERSION" + the new CHANGELOG section body.
# --cleanup=verbatim preserves '### Added' / '### Changed' / '### Fixed' headers
# (default cleanup strips lines starting with '#').
git add "$BASH_FILE" "$CHANGELOG"
{ printf 'Release v%s\n\n' "$VERSION"; cat "$RELEASE_BODY"; } \
    | git commit --cleanup=verbatim -F -
echo "[committed] $(git log -1 --pretty='%h %s')"

# 8 — annotated tag with the same body.
{ printf 'Release v%s\n\n' "$VERSION"; cat "$RELEASE_BODY"; } \
    | git tag -a --cleanup=verbatim -F - "$TAG"
echo "[tagged]    $TAG → $(git rev-parse --short "$TAG")"

# 9 — push (or print). Pushes the current branch (not a hardcoded `main`)
# so this works whether you released from `main` or a long-lived release
# branch.
CURRENT_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo HEAD)"
if [ "$CURRENT_BRANCH" = "HEAD" ]; then
    echo "ERROR: detached HEAD — release commit is unreachable from any branch." >&2
    echo "       Checkout a branch first, then re-run." >&2
    exit 1
fi
if $PUSH; then
    echo
    echo "Pushing $CURRENT_BRANCH + tag…"
    git push origin "$CURRENT_BRANCH"
    git push origin "$TAG"
    echo "Done."
else
    echo
    echo "Not pushing. To publish:"
    echo "  git push origin $CURRENT_BRANCH && git push origin $TAG"
fi
