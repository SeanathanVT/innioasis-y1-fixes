#!/usr/bin/env python3
"""
patch_y1_apk.py  —  Innioasis Y1 Artist→Album navigation patch
================================================================
Patches a Y1 media player APK so that selecting an Artist shows
that artist's Albums (with cover art) before listing songs,
instead of jumping straight to a flat song list.

Compatible with: 3.0.2, 3.0.7, and future minor versions.

REQUIREMENTS
------------
  Python 3.8+
  Java 11+  (for apktool's smali assembler)
  apktool   (downloaded automatically if not found)
  pip packages: androguard
    pip install androguard

USAGE
-----
  python3 patch_y1_apk.py <path/to/com_innioasis_y1_X_X_X.apk>

  If no argument is given, the script looks for any
  com_innioasis_y1_*.apk in the current directory.

Produces:  com.innioasis.y1_<version>-patched.apk  (unsigned)

DEPLOYMENT
----------
  The output APK is unsigned. It must be deployed directly to the
  device filesystem — not installed via PackageManager — because
  com.innioasis.y1 is a system app and signature verification
  would reject a re-signed APK.

  Option A — ADB push (requires root / remounted /system):
    adb root
    adb remount
    adb push com.innioasis.y1_<version>-patched.apk \\
        /system/app/com.innioasis.y1/com.innioasis.y1.apk
    adb shell chmod 644 \\
        /system/app/com.innioasis.y1/com.innioasis.y1.apk
    adb reboot

  Option B — Firmware flash:
    Replace the APK inside the stock firmware image
    (under /system/app/) and reflash via MTK scatter tool.

  Do NOT use `adb install` or sideload via a file manager —
  PackageManager will reject the APK due to signature mismatch.
"""

import os, sys, re, shutil, subprocess, urllib.request, zipfile
import tempfile, glob
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────────────────
WORK_DIR      = "_patch_workdir"
# Prefer apktool from npm package if available (known-good 2.0.3)
_NPM_APKTOOL  = "/home/claude/.npm-global/lib/node_modules/apktool/bin/apktool.jar"
APKTOOL_JAR   = _NPM_APKTOOL if os.path.exists(_NPM_APKTOOL) else os.path.join(WORK_DIR, "apktool.jar")
APKTOOL_URL   = "https://github.com/iBotPeaches/Apktool/releases/download/v2.9.3/apktool_2.9.3.jar"
UNPACKED_DIR  = os.path.join(WORK_DIR, "unpacked")

ARTISTS_SMALI = "smali_classes2/com/innioasis/music/ArtistsActivity.smali"
ALBUMS_SMALI  = "smali_classes2/com/innioasis/music/AlbumsActivity.smali"

# ── Helpers ──────────────────────────────────────────────────────────────────
def run(cmd, **kw):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if result.returncode != 0:
        print(f"STDOUT: {result.stdout[-2000:]}")
        print(f"STDERR: {result.stderr[-2000:]}")
        sys.exit(f"Command failed (exit {result.returncode})")
    return result

def find_java():
    for candidate in ["java",
                      "/usr/lib/jvm/java-21-openjdk-amd64/bin/java",
                      "/usr/lib/jvm/java-17-openjdk-amd64/bin/java",
                      "/usr/lib/jvm/default-java/bin/java"]:
        if shutil.which(candidate):
            return candidate
    sys.exit("ERROR: Java not found. Install Java 11+ and ensure 'java' is on PATH.")

def get_apk_info(apk_path: str):
    """Extract package name and version from binary AndroidManifest.xml."""
    try:
        from androguard.core.apk import APK
        import logging
        logging.getLogger("androguard").setLevel(logging.ERROR)
        apk = APK(apk_path)
        return apk.get_package(), apk.get_androidversion_name()
    except Exception:
        pass
    # Fallback: scan binary manifest for UTF-16LE strings
    with zipfile.ZipFile(apk_path) as z:
        data = z.read("AndroidManifest.xml")
    text = data.decode('utf-16-le', errors='replace')
    pkg = re.search(r'(com\.innioasis\.[a-z0-9_]+)', text)
    ver = re.search(r'(\d+\.\d+\.\d+)', text)
    return (pkg.group(1) if pkg else "com.innioasis.y1",
            ver.group(1) if ver else "unknown")

# ── Step 0: Pre-flight ───────────────────────────────────────────────────────
print("=" * 60)
print("Innioasis Y1 Artist→Album patch  (multi-version)")
print("=" * 60)

# Resolve input APK
if len(sys.argv) >= 2:
    ORIGINAL_APK = sys.argv[1]
else:
    candidates = sorted(glob.glob("com_innioasis_y1_*.apk") +
                        glob.glob("com.innioasis.y1_*.apk"))
    if not candidates:
        sys.exit("ERROR: No APK specified and none found in current directory.\n"
                 "  Usage: python3 patch_y1_apk.py <path/to/apk>")
    ORIGINAL_APK = candidates[0]
    print(f"  Auto-detected APK: {ORIGINAL_APK}")

if not os.path.exists(ORIGINAL_APK):
    sys.exit(f"ERROR: '{ORIGINAL_APK}' not found.")

# Get package name + version for output filename
pkg_name, version = get_apk_info(ORIGINAL_APK)
OUTPUT_APK = f"{pkg_name}_{version}-patched.apk"
print(f"  Package:  {pkg_name}")
print(f"  Version:  {version}")
print(f"  Output:   {OUTPUT_APK}  (unsigned — deploy via ADB push or firmware flash)")

java = find_java()
print(f"✓ Java found: {java}")
os.makedirs(WORK_DIR, exist_ok=True)

# ── Step 1: Locate or download apktool ───────────────────────────────────────
if APKTOOL_JAR == _NPM_APKTOOL:
    print(f"\n[1/4] Using bundled apktool ({os.path.getsize(APKTOOL_JAR):,} bytes)")
elif not os.path.exists(APKTOOL_JAR) or os.path.getsize(APKTOOL_JAR) < 1_000_000:
    print(f"\n[1/4] Downloading apktool from GitHub...")
    try:
        urllib.request.urlretrieve(APKTOOL_URL, APKTOOL_JAR)
        print(f"      Saved {os.path.getsize(APKTOOL_JAR):,} bytes → {APKTOOL_JAR}")
    except Exception as e:
        sys.exit(f"ERROR downloading apktool: {e}\n"
                 f"  Manual fix: download {APKTOOL_URL}\n"
                 f"  and place it at {APKTOOL_JAR}")
else:
    print(f"\n[1/4] apktool.jar already present ({os.path.getsize(APKTOOL_JAR):,} bytes)")

# ── Step 2: Unpack APK ───────────────────────────────────────────────────────
print(f"\n[2/4] Unpacking APK with apktool (smali decode, no resources)...")
if os.path.exists(UNPACKED_DIR):
    shutil.rmtree(UNPACKED_DIR)
run([java, "-jar", APKTOOL_JAR, "d",
     "--no-res",
     "-f",
     ORIGINAL_APK,
     "-o", UNPACKED_DIR])
print(f"      Unpacked to {UNPACKED_DIR}/")

# ── Step 3: Apply smali patches ──────────────────────────────────────────────
print(f"\n[3/4] Patching smali files...")

# ── Patch A: ArtistsActivity.smali ───────────────────────────────────────────
# The target block is structurally identical across all known versions.
# The only version variation (resource ID constants) is NOT in this block.

artists_path = os.path.join(UNPACKED_DIR, ARTISTS_SMALI)
if not os.path.exists(artists_path):
    sys.exit(f"ERROR: Expected smali not found: {artists_path}")

with open(artists_path, 'r') as f:
    artists_src = f.read()

OLD_ARTISTS = """\
    :cond_2
    invoke-virtual {p0}, Lcom/innioasis/music/ArtistsActivity;->isShowArtists()Z

    move-result v0

    if-eqz v0, :cond_3

    .line 105
    invoke-direct {p0}, Lcom/innioasis/music/ArtistsActivity;->getAdapter()Lcom/innioasis/music/adapter/MainAdapter;

    move-result-object v0

    invoke-virtual {v0}, Lcom/innioasis/music/adapter/MainAdapter;->getPosition()I

    move-result v0

    .line 106
    invoke-direct {p0}, Lcom/innioasis/music/ArtistsActivity;->getAdapter()Lcom/innioasis/music/adapter/MainAdapter;

    move-result-object v1

    invoke-virtual {v1, v0}, Lcom/innioasis/music/adapter/MainAdapter;->getItem(I)Ljava/lang/Object;

    move-result-object v0

    check-cast v0, Ljava/lang/String;

    iput-object v0, p0, Lcom/innioasis/music/ArtistsActivity;->artist:Ljava/lang/String;

    .line 107
    sget-object v0, Lcom/innioasis/y1/database/Y1Repository$SongSortType;->Companion:Lcom/innioasis/y1/database/Y1Repository$SongSortType$Companion;

    sget-object v1, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->INSTANCE:Lcom/innioasis/y1/utils/SharedPreferencesUtils;

    invoke-virtual {v1}, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->getSortArtistSong()I

    move-result v1

    invoke-virtual {v0, v1}, Lcom/innioasis/y1/database/Y1Repository$SongSortType$Companion;->fromType(I)Lcom/innioasis/y1/database/Y1Repository$SongSortType;

    move-result-object v0

    .line 108
    iget-object v1, p0, Lcom/innioasis/music/ArtistsActivity;->artist:Ljava/lang/String;

    invoke-direct {p0, v1, v0}, Lcom/innioasis/music/ArtistsActivity;->switchSongSortType(Ljava/lang/String;Lcom/innioasis/y1/database/Y1Repository$SongSortType;)V

    .line 109
    invoke-virtual {p0}, Lcom/innioasis/music/ArtistsActivity;->getVb()Landroidx/viewbinding/ViewBinding;

    move-result-object v0

    check-cast v0, Lcom/innioasis/y1/databinding/ActivityArtistsBinding;

    iget-object v0, v0, Lcom/innioasis/y1/databinding/ActivityArtistsBinding;->spv:Lcom/innioasis/y1/view/ShufflePlaylistItemView;

    invoke-virtual {v0}, Lcom/innioasis/y1/view/ShufflePlaylistItemView;->show()V

    goto :goto_1"""

NEW_ARTISTS = """\
    :cond_2
    invoke-virtual {p0}, Lcom/innioasis/music/ArtistsActivity;->isShowArtists()Z

    move-result v0

    if-eqz v0, :cond_3

    .line 105
    invoke-direct {p0}, Lcom/innioasis/music/ArtistsActivity;->getAdapter()Lcom/innioasis/music/adapter/MainAdapter;

    move-result-object v0

    invoke-virtual {v0}, Lcom/innioasis/music/adapter/MainAdapter;->getPosition()I

    move-result v0

    .line 106
    invoke-direct {p0}, Lcom/innioasis/music/ArtistsActivity;->getAdapter()Lcom/innioasis/music/adapter/MainAdapter;

    move-result-object v1

    invoke-virtual {v1, v0}, Lcom/innioasis/music/adapter/MainAdapter;->getItem(I)Ljava/lang/Object;

    move-result-object v0

    check-cast v0, Ljava/lang/String;

    iput-object v0, p0, Lcom/innioasis/music/ArtistsActivity;->artist:Ljava/lang/String;

    .line 108
    new-instance v1, Landroid/content/Intent;

    invoke-virtual {p0}, Lcom/innioasis/music/ArtistsActivity;->getContext()Landroid/content/Context;

    move-result-object v2

    const-class v3, Lcom/innioasis/music/AlbumsActivity;

    invoke-direct {v1, v2, v3}, Landroid/content/Intent;-><init>(Landroid/content/Context;Ljava/lang/Class;)V

    const-string v2, "ARTIST"

    iget-object v3, p0, Lcom/innioasis/music/ArtistsActivity;->artist:Ljava/lang/String;

    invoke-virtual {v1, v2, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;

    invoke-virtual {p0, v1}, Lcom/innioasis/music/ArtistsActivity;->startActivity(Landroid/content/Intent;)V

    goto :goto_1"""

if OLD_ARTISTS not in artists_src:
    sys.exit("ERROR: ArtistsActivity patch target not found — "
             "wrong APK version or already patched?")
artists_src = artists_src.replace(OLD_ARTISTS, NEW_ARTISTS, 1)
with open(artists_path, 'w') as f:
    f.write(artists_src)
print("  ✓ Patch A: ArtistsActivity — artist tap now launches AlbumsActivity")

# ── Patch B: AlbumsActivity.smali ────────────────────────────────────────────
albums_path = os.path.join(UNPACKED_DIR, ALBUMS_SMALI)
if not os.path.exists(albums_path):
    sys.exit(f"ERROR: Expected smali not found: {albums_path}")

with open(albums_path, 'r') as f:
    albums_src = f.read()

# B1: Add artist field
OLD_FIELD = ".field private albumName:Ljava/lang/String;"
NEW_FIELD  = ".field private albumName:Ljava/lang/String;\n\n.field private artist:Ljava/lang/String;"
if OLD_FIELD not in albums_src:
    sys.exit("ERROR: AlbumsActivity field target not found.")
albums_src = albums_src.replace(OLD_FIELD, NEW_FIELD, 1)
print("  ✓ Patch B1: AlbumsActivity — added artist field")

# B2: Replace initView() — match with regex to handle version-varying resource ID
# Between versions, ONLY the resource ID hex constant on the `const v0, 0x7f11XXXX`
# line changes. We match it with a wildcard and preserve the original value.
INIT_VIEW_PATTERN = re.compile(
    r'(\.method public initView\(\)V\n'
    r'    \.locals 2\n'
    r'\n'
    r'    )(const v0, 0x7f11[0-9a-f]{4})'   # ← version-varying resource ID
    r'(\n'
    r'\n'
    r'    \.line 50\n'
    r'    invoke-virtual \{p0, v0\}, Lcom/innioasis/music/AlbumsActivity;->getString\(I\)Ljava/lang/String;\n'
    r'\n'
    r'    move-result-object v0\n'
    r'\n'
    r'    const-string v1, "getString\(R\.string\.music_albums\)"\n'
    r'\n'
    r'    invoke-static \{v0, v1\}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullExpressionValue\(Ljava/lang/Object;Ljava/lang/String;\)V\n'
    r'\n'
    r'    invoke-virtual \{p0, v0\}, Lcom/innioasis/music/AlbumsActivity;->setStateBarLeftText\(Ljava/lang/String;\)V\n'
    r'\n'
    r'    \.line 51\n'
    r'    invoke-virtual \{p0\}, Lcom/innioasis/music/AlbumsActivity;->getVb\(\)Landroidx/viewbinding/ViewBinding;\n'
    r'\n'
    r'    move-result-object v0\n'
    r'\n'
    r'    check-cast v0, Lcom/innioasis/y1/databinding/ActivityAlbumsBinding;\n'
    r'\n'
    r'    iget-object v0, v0, Lcom/innioasis/y1/databinding/ActivityAlbumsBinding;->lv:Landroid/widget/ListView;\n'
    r'\n'
    r'    invoke-direct \{p0\}, Lcom/innioasis/music/AlbumsActivity;->getAdapter\(\)Lcom/innioasis/music/adapter/AlbumListAdapter;\n'
    r'\n'
    r'    move-result-object v1\n'
    r'\n'
    r'    check-cast v1, Landroid/widget/ListAdapter;\n'
    r'\n'
    r'    invoke-virtual \{v0, v1\}, Landroid/widget/ListView;->setAdapter\(Landroid/widget/ListAdapter;\)V\n'
    r'\n'
    r'    \.line 52\n'
    r'    invoke-virtual \{p0\}, Lcom/innioasis/music/AlbumsActivity;->getVb\(\)Landroidx/viewbinding/ViewBinding;\n'
    r'\n'
    r'    move-result-object v0\n'
    r'\n'
    r'    check-cast v0, Lcom/innioasis/y1/databinding/ActivityAlbumsBinding;\n'
    r'\n'
    r'    iget-object v0, v0, Lcom/innioasis/y1/databinding/ActivityAlbumsBinding;->spv:Lcom/innioasis/y1/view/ShufflePlaylistItemView;\n'
    r'\n'
    r'    invoke-direct \{p0\}, Lcom/innioasis/music/AlbumsActivity;->getSongAdapter\(\)Lcom/innioasis/music/adapter/SongListAdapter;\n'
    r'\n'
    r'    move-result-object v1\n'
    r'\n'
    r'    check-cast v1, Lcom/innioasis/music/adapter/MyBaseAdapter;\n'
    r'\n'
    r'    invoke-virtual \{v0, v1\}, Lcom/innioasis/y1/view/ShufflePlaylistItemView;->bind\(Lcom/innioasis/music/adapter/MyBaseAdapter;\)V\n'
    r'\n'
    r'    \.line 53\n'
    r'    sget-object v0, Lcom/innioasis/y1/database/Y1Repository\$SortAlbumType;->Companion:Lcom/innioasis/y1/database/Y1Repository\$SortAlbumType\$Companion;\n'
    r'\n'
    r'    sget-object v1, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->INSTANCE:Lcom/innioasis/y1/utils/SharedPreferencesUtils;\n'
    r'\n'
    r'    invoke-virtual \{v1\}, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->getSortAlbum\(\)I\n'
    r'\n'
    r'    move-result v1\n'
    r'\n'
    r'    invoke-virtual \{v0, v1\}, Lcom/innioasis/y1/database/Y1Repository\$SortAlbumType\$Companion;->fromType\(I\)Lcom/innioasis/y1/database/Y1Repository\$SortAlbumType;\n'
    r'\n'
    r'    move-result-object v0\n'
    r'\n'
    r'    invoke-direct \{p0, v0\}, Lcom/innioasis/music/AlbumsActivity;->getAlbumListBySort\(Lcom/innioasis/y1/database/Y1Repository\$SortAlbumType;\)V\n'
    r'\n'
    r'    return-void\n'
    r'\.end method)',
    re.MULTILINE
)

m = INIT_VIEW_PATTERN.search(albums_src)
if not m:
    sys.exit("ERROR: AlbumsActivity initView target not found — "
             "wrong APK version or already patched?")

# Preserve the exact resource ID constant from this version
res_id = m.group(2)   # e.g. "const v0, 0x7f110121" or "const v0, 0x7f110123"
print(f"  ✓ Detected initView resource ID: {res_id.split()[-1]}")

NEW_INIT_VIEW = f""".method public initView()V
    .locals 4

    {res_id}

    .line 50
    invoke-virtual {{p0, v0}}, Lcom/innioasis/music/AlbumsActivity;->getString(I)Ljava/lang/String;

    move-result-object v0

    const-string v1, "getString(R.string.music_albums)"

    invoke-static {{v0, v1}}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullExpressionValue(Ljava/lang/Object;Ljava/lang/String;)V

    invoke-virtual {{p0, v0}}, Lcom/innioasis/music/AlbumsActivity;->setStateBarLeftText(Ljava/lang/String;)V

    .line 51
    invoke-virtual {{p0}}, Lcom/innioasis/music/AlbumsActivity;->getVb()Landroidx/viewbinding/ViewBinding;

    move-result-object v0

    check-cast v0, Lcom/innioasis/y1/databinding/ActivityAlbumsBinding;

    iget-object v0, v0, Lcom/innioasis/y1/databinding/ActivityAlbumsBinding;->lv:Landroid/widget/ListView;

    invoke-direct {{p0}}, Lcom/innioasis/music/AlbumsActivity;->getAdapter()Lcom/innioasis/music/adapter/AlbumListAdapter;

    move-result-object v1

    check-cast v1, Landroid/widget/ListAdapter;

    invoke-virtual {{v0, v1}}, Landroid/widget/ListView;->setAdapter(Landroid/widget/ListAdapter;)V

    .line 52
    invoke-virtual {{p0}}, Lcom/innioasis/music/AlbumsActivity;->getVb()Landroidx/viewbinding/ViewBinding;

    move-result-object v0

    check-cast v0, Lcom/innioasis/y1/databinding/ActivityAlbumsBinding;

    iget-object v0, v0, Lcom/innioasis/y1/databinding/ActivityAlbumsBinding;->spv:Lcom/innioasis/y1/view/ShufflePlaylistItemView;

    invoke-direct {{p0}}, Lcom/innioasis/music/AlbumsActivity;->getSongAdapter()Lcom/innioasis/music/adapter/SongListAdapter;

    move-result-object v1

    check-cast v1, Lcom/innioasis/music/adapter/MyBaseAdapter;

    invoke-virtual {{v0, v1}}, Lcom/innioasis/y1/view/ShufflePlaylistItemView;->bind(Lcom/innioasis/music/adapter/MyBaseAdapter;)V

    .line 53
    invoke-virtual {{p0}}, Lcom/innioasis/music/AlbumsActivity;->getIntent()Landroid/content/Intent;

    move-result-object v0

    const-string v1, "ARTIST"

    invoke-virtual {{v0, v1}}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v0

    if-eqz v0, :cond_no_artist

    const-string v1, ""

    invoke-virtual {{v0, v1}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v1

    if-nez v1, :cond_no_artist

    iput-object v0, p0, Lcom/innioasis/music/AlbumsActivity;->artist:Ljava/lang/String;

    sget-object v1, Lcom/innioasis/y1/Y1Application;->Companion:Lcom/innioasis/y1/Y1Application$Companion;

    invoke-virtual {{v1}}, Lcom/innioasis/y1/Y1Application$Companion;->getY1Repository()Lcom/innioasis/y1/database/Y1Repository;

    move-result-object v1

    invoke-virtual {{v1, v0}}, Lcom/innioasis/y1/database/Y1Repository;->getAlbumsByKeySync(Ljava/lang/String;)Ljava/util/List;

    move-result-object v2

    invoke-direct {{p0}}, Lcom/innioasis/music/AlbumsActivity;->getAdapter()Lcom/innioasis/music/adapter/AlbumListAdapter;

    move-result-object v3

    invoke-virtual {{v3, v2}}, Lcom/innioasis/music/adapter/AlbumListAdapter;->setItems(Ljava/util/List;)V

    return-void

    :cond_no_artist
    sget-object v0, Lcom/innioasis/y1/database/Y1Repository$SortAlbumType;->Companion:Lcom/innioasis/y1/database/Y1Repository$SortAlbumType$Companion;

    sget-object v1, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->INSTANCE:Lcom/innioasis/y1/utils/SharedPreferencesUtils;

    invoke-virtual {{v1}}, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->getSortAlbum()I

    move-result v1

    invoke-virtual {{v0, v1}}, Lcom/innioasis/y1/database/Y1Repository$SortAlbumType$Companion;->fromType(I)Lcom/innioasis/y1/database/Y1Repository$SortAlbumType;

    move-result-object v0

    invoke-direct {{p0, v0}}, Lcom/innioasis/music/AlbumsActivity;->getAlbumListBySort(Lcom/innioasis/y1/database/Y1Repository$SortAlbumType;)V

    return-void
.end method"""

albums_src = INIT_VIEW_PATTERN.sub(NEW_INIT_VIEW, albums_src, count=1)
with open(albums_path, 'w') as f:
    f.write(albums_src)
print("  ✓ Patch B2: AlbumsActivity — initView reads ARTIST intent extra")

# ── Step 4: Reassemble DEX with apktool ──────────────────────────────────────
print(f"\n[4/4] Reassembling smali → DEX (this takes ~30 seconds)...")
# apktool builds smali→DEX first, then tries aapt for resources.
# Since we decoded with --no-res, the aapt/resource step fails — but the DEX
# is already built by that point. We ignore the exit code intentionally.
subprocess.run(
    [java, "-jar", APKTOOL_JAR, "b", UNPACKED_DIR],
    capture_output=True, text=True
)  # non-zero exit expected (aapt fails); DEX is still produced

dex1 = os.path.join(UNPACKED_DIR, "build", "apk", "classes.dex")
dex2 = os.path.join(UNPACKED_DIR, "build", "apk", "classes2.dex")
if not os.path.exists(dex1) or not os.path.exists(dex2):
    sys.exit("ERROR: DEX assembly failed — classes.dex or classes2.dex not produced.")
print(f"  ✓ classes.dex  {os.path.getsize(dex1):,} bytes")
print(f"  ✓ classes2.dex {os.path.getsize(dex2):,} bytes")

# ── Build unsigned APK (replace DEX in original zip) ─────────────────────────
with open(dex1, 'rb') as f: dex1_bytes = f.read()
with open(dex2, 'rb') as f: dex2_bytes = f.read()

with zipfile.ZipFile(ORIGINAL_APK, 'r') as zin:
    with zipfile.ZipFile(OUTPUT_APK, 'w',
                         compression=zipfile.ZIP_DEFLATED,
                         allowZip64=True) as zout:
        for item in zin.infolist():
            if item.filename.startswith('META-INF/'):
                continue          # strip original signature
            if item.filename == 'classes.dex':
                zout.writestr(item, dex1_bytes)
            elif item.filename == 'classes2.dex':
                zout.writestr(item, dex2_bytes)
            else:
                zout.writestr(item, zin.read(item.filename))

size = os.path.getsize(OUTPUT_APK)
print(f"  ✓ Unsigned APK: {OUTPUT_APK} ({size:,} bytes)")

# ── Done ──────────────────────────────────────────────────────────────────────
print(f"""
{'=' * 60}
SUCCESS
{'=' * 60}
Output:  {OUTPUT_APK}  (unsigned)

Deploy via ADB push (requires root / remounted /system):
  adb root
  adb remount
  adb push {OUTPUT_APK} /system/app/com.innioasis.y1/com.innioasis.y1.apk
  adb shell chmod 644 /system/app/com.innioasis.y1/com.innioasis.y1.apk
  adb reboot

Do NOT use `adb install` — PackageManager will reject the APK
due to signature mismatch (com.innioasis.y1 is a system app).
{'=' * 60}
""")
