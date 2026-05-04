#!/usr/bin/env bash
# release.sh — bump the bash's # Version: header, fold [Unreleased] CHANGELOG
# entries into a new version section, commit, and tag.
#
# What it does (in order):
#   1. Validate args: $1 is a semver (X.Y.Z); --push is optional.
#   2. Check the working tree is clean.
#   3. Check no tag named v$VERSION already exists.
#   4. Check CHANGELOG.md has a [Unreleased] section with at least one bullet
#      under it (refuse to release an empty CHANGELOG entry).
#   5. Update the bash's `# Version:` header in-place.
#   6. Rename `## [Unreleased]` to `## [$VERSION] - YYYY-MM-DD` in CHANGELOG.md
#      and prepend a fresh empty `## [Unreleased]` block above it.
#   7. Commit both files with message "Release v$VERSION. See CHANGELOG.md for notes."
#   8. Create annotated tag `v$VERSION` at HEAD with the same message.
#   9. If --push given: push the commit and the tag together. Otherwise print
#      the commands you'd run to push later.
#
# Exits non-zero on any precondition failure; never partial-applies.
#
# Usage:
#   ./tools/release.sh 1.11.0
#   ./tools/release.sh 1.11.0 --push
#
# After:
#   git log --oneline --decorate=short --simplify-by-decoration | head
#   # → shows v1.11.0 alongside the rest of the version anchors

set -euo pipefail

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    echo "usage: $0 <semver> [--push]" >&2
    echo "  e.g. $0 1.11.0" >&2
    echo "  e.g. $0 1.11.0 --push" >&2
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

# Show what's about to ship — last chance to abort.
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
    echo "Then: git push origin main && git push origin $TAG."
else
    echo "(push: not requested; will print commands for later)"
fi
echo

# 5 — bump the bash's # Version: header (in-place, but verify after)
if ! grep -qE '^# Version:[[:space:]]+[0-9]+\.[0-9]+\.[0-9]+[[:space:]]*$' "$BASH_FILE"; then
    echo "ERROR: $BASH_FILE has no '# Version:' header in the expected format." >&2
    exit 1
fi
sed -i.bak -E "s/^# Version:[[:space:]]+[0-9]+\\.[0-9]+\\.[0-9]+[[:space:]]*$/# Version:   $VERSION/" "$BASH_FILE"
rm -f "$BASH_FILE.bak"
if ! grep -qE "^# Version:[[:space:]]+$VERSION[[:space:]]*$" "$BASH_FILE"; then
    echo "ERROR: bash version bump didn't take. Check $BASH_FILE." >&2
    exit 1
fi
echo "[bumped] $BASH_FILE  '# Version: $VERSION'"

# 6 — rename [Unreleased] → [$VERSION] and add new empty [Unreleased] above
python3 - "$CHANGELOG" "$VERSION" "$TODAY" <<'PYEOF'
import sys, re, pathlib
path, version, today = sys.argv[1], sys.argv[2], sys.argv[3]
text = pathlib.Path(path).read_text()
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

# 7 — commit
COMMIT_MSG="Release v$VERSION. See CHANGELOG.md for notes."
git add "$BASH_FILE" "$CHANGELOG"
git commit -m "$COMMIT_MSG"
echo "[committed] $(git log -1 --pretty='%h %s')"

# 8 — annotated tag
git tag -a "$TAG" -m "Release $VERSION. See CHANGELOG.md for notes."
echo "[tagged]    $TAG → $(git rev-parse --short "$TAG")"

# 9 — push (or print)
if $PUSH; then
    echo
    echo "Pushing main + tag…"
    git push origin main
    git push origin "$TAG"
    echo "Done."
else
    echo
    echo "Not pushing. To publish:"
    echo "  git push origin main && git push origin $TAG"
fi
