#!/usr/bin/env python3
"""
patch_y1_apk.py  —  Innioasis Y1 Artist->Album navigation patch
================================================================
Patches a Y1 media player APK so that selecting an Artist shows
that artist's Albums (with cover art) before listing songs,
instead of jumping straight to a flat song list.

Verified against: 3.0.2 (DEX-level analysis performed on actual binary)

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

Produces:  com.innioasis.y1_<version>-patched.apk

  The original META-INF/ signature block is retained from the stock APK.
  PackageManager requires a parseable signature block to be present at boot
  even for system apps pushed directly to /system/app/ -- a completely
  unsigned zip triggers "no certificates" rejection. The stale signature
  is harmless since cert verification is bypassed when pushing via ADB.

DEPLOYMENT
----------
  The output APK must be deployed directly to the device filesystem --
  not installed via PackageManager -- because com.innioasis.y1 is a
  system app and signature verification would reject a re-signed APK.

  Option A -- ADB push (requires root / remounted /system):
    adb root
    adb remount
    adb push com.innioasis.y1_<version>-patched.apk \\
        /system/app/com.innioasis.y1/com.innioasis.y1.apk
    adb shell chmod 644 \\
        /system/app/com.innioasis.y1/com.innioasis.y1.apk
    adb reboot

  Option B -- Firmware flash:
    Replace the APK inside the stock firmware image
    (under /system/app/) and reflash via MTK scatter tool.

  Do NOT use `adb install` or sideload via a file manager --
  PackageManager will reject the APK due to signature mismatch.

WHAT THIS PATCH DOES
--------------------
  Two smali edits, no new files, no Manifest changes:

  Patch A -- ArtistsActivity.confirm():
    When the user taps an artist row (isShowArtists()==true,
    isMultiSelect==false), the original code calls switchSongSortType()
    which navigates to a flat song list. The patch replaces this with an
    Intent launching AlbumsActivity, passing the artist name via the
    "artist_key" extra. All other branches are unchanged.

  Patch B -- AlbumsActivity.initView():
    After the existing setup (title, ListView adapter, SPV bind), reads
    the "artist_key" Intent extra. If present and non-empty, calls
    SongDao.getSongsByArtistSortByAlbum(artist) -- which runs
    SELECT * FROM song WHERE artist = ? ORDER BY lower(pinyinAlbum) --
    deduplicates the returned Song list by album name using a LinkedHashSet,
    builds an ArrayList<String> of unique album names in album sort order,
    then calls AlbumListAdapter.setAlbums() and returns.
    If the extra is absent, falls through to the original getAlbumListBySort()
    call, preserving the normal Albums screen behavior.

DEX ANALYSIS FACTS (verified from actual 3.0.2 binary)
-------------------------------------------------------
  ArtistsActivity.confirm():
    registers_size=5; p0=this=v4
    Artist-tap branch at instructions 53-79 (isShowArtists true, not multiselect)
    ArtistsActivity.artist field stores the selected artist name (Ljava/lang/String;)
    switchSongSortType() call is at instructions 72-73 -- this is what we replace

  AlbumsActivity.initView():
    registers_size=3; p0=this=v2; locals=2 original (patched to .locals 8)
    Resource ID const: 2131820833 (0x7f110121)
    getAlbumListBySort() launches a coroutine (async) -- safe to skip via early return

  Y1Repository.getAlbumsByKey(String)List -- NOT used:
    Queries album column LIKE '%key%' -- searches by album name substring.
    Passing an artist name returns albums whose title contains the artist name,
    which produces empty results. This method is NOT the correct one to use.

  SongDao.getSongsByArtistSortByAlbum(String)List -- CORRECT method:
    SQL: SELECT * FROM song WHERE isAudiobook = 0 AND artist = ?
         ORDER BY lower(pinyinAlbum)
    Returns List<Song> for an exact artist match, sorted by album.
    SongDao is accessed via Y1Repository.access$getSongDao$p(repo) -- a Kotlin
    compiler-generated static accessor. The songDao field itself is private and
    cannot be read via iget-object from outside Y1Repository (IllegalAccessError).
    The accessor exists in the DEX but exhibits NoSuchMethodError on this device's
    old Dalvik (API 17). Instead, Patch C makes songDao public so iget-object works.
    Song.getAlbum() returns Ljava/lang/String;.

  AlbumListAdapter.setAlbums(List)V:
    EXISTS. Takes List<String> (album names). Correct method name is setAlbums.

  Intent extra key for album->song drill-down:
    ShowSongListActivity reads "album_name" (lowercase, underscore).
    AlbumsActivity.confirm()->switchSongSortType() uses this key internally.
    No changes needed to the album->song navigation flow.

  "ARTIST" / "ALBUM_NAME" string constants: NOT present in this DEX.
    We use "artist_key" to avoid any collision with existing strings.
"""

import os, sys, re, shutil, subprocess, urllib.request, zipfile
import glob
from collections import Counter

# -- Config -------------------------------------------------------------------
WORK_DIR      = "_patch_workdir"
_NPM_APKTOOL  = "/home/claude/.npm-global/lib/node_modules/apktool/bin/apktool.jar"
APKTOOL_JAR   = _NPM_APKTOOL if os.path.exists(_NPM_APKTOOL) else os.path.join(WORK_DIR, "apktool.jar")
APKTOOL_URL   = "https://github.com/iBotPeaches/Apktool/releases/download/v2.9.3/apktool_2.9.3.jar"
UNPACKED_DIR  = os.path.join(WORK_DIR, "unpacked")

ARTISTS_SMALI = "smali_classes2/com/innioasis/music/ArtistsActivity.smali"
ALBUMS_SMALI  = "smali_classes2/com/innioasis/music/AlbumsActivity.smali"
REPO_SMALI    = "smali/com/innioasis/y1/database/Y1Repository.smali"

# Intent extra key we inject. Verified absent from 3.0.2 DEX string pool.
ARTIST_INTENT_KEY = "artist_key"

# -- Helpers ------------------------------------------------------------------
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
    # Fallback: scan binary manifest for UTF-16LE strings.
    # Use most-frequent match to avoid picking up incidental package name
    # strings (e.g. com.innioasis.fm) that appear before the declared package.
    with zipfile.ZipFile(apk_path) as z:
        data = z.read("AndroidManifest.xml")
    text = data.decode('utf-16-le', errors='replace')
    matches = re.findall(r'(com\.innioasis\.[a-z0-9_]+)', text)
    pkg = Counter(matches).most_common(1)[0][0] if matches else "com.innioasis.y1"
    ver = re.search(r'(\d+\.\d+\.\d+)', text)
    return (pkg, ver.group(1) if ver else "unknown")

# -- Step 0: Pre-flight -------------------------------------------------------
print("=" * 60)
print("Innioasis Y1 Artist->Album patch")
print("=" * 60)

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

pkg_name, version = get_apk_info(ORIGINAL_APK)
os.makedirs("output", exist_ok=True)
OUTPUT_APK = os.path.join("output", f"{pkg_name}_{version}-patched.apk")
print(f"  Package:  {pkg_name}")
print(f"  Version:  {version}")
print(f"  Output:   {OUTPUT_APK}")

java = find_java()
print(f"  Java:     {java}")

# -- Step 1: Locate or download apktool ---------------------------------------
os.makedirs(WORK_DIR, exist_ok=True)

if APKTOOL_JAR == _NPM_APKTOOL:
    print(f"\n[1/4] Using bundled apktool ({os.path.getsize(APKTOOL_JAR):,} bytes)")
elif not os.path.exists(APKTOOL_JAR) or os.path.getsize(APKTOOL_JAR) < 1_000_000:
    print(f"\n[1/4] Downloading apktool from GitHub...")
    try:
        urllib.request.urlretrieve(APKTOOL_URL, APKTOOL_JAR)
        print(f"      Saved {os.path.getsize(APKTOOL_JAR):,} bytes -> {APKTOOL_JAR}")
    except Exception as e:
        sys.exit(f"ERROR downloading apktool: {e}\n"
                 f"  Manual fix: download {APKTOOL_URL}\n"
                 f"  and place it at {APKTOOL_JAR}")
else:
    print(f"\n[1/4] apktool.jar already present ({os.path.getsize(APKTOOL_JAR):,} bytes)")

# -- Step 2: Unpack APK -------------------------------------------------------
print(f"\n[2/4] Unpacking APK with apktool...")
if os.path.exists(UNPACKED_DIR):
    shutil.rmtree(UNPACKED_DIR)
run([java, "-jar", APKTOOL_JAR, "d", "--no-res", "-f",
     ORIGINAL_APK, "-o", UNPACKED_DIR])
print(f"      Unpacked to {UNPACKED_DIR}/")

# -- Step 3: Apply smali patches ----------------------------------------------
print(f"\n[3/4] Patching smali files...")

# ============================================================
# Patch A: ArtistsActivity.smali
# ============================================================
#
# In confirm(), when the user taps an artist (isShowArtists==true,
# isMultiSelect==false), the original code block is:
#
#   .line 107
#   sget-object v0, Y1Repository$SongSortType;->Companion ...
#   sget-object v1, SharedPreferencesUtils;->INSTANCE ...
#   invoke-virtual {v1}, ...getSortArtistSong()I
#   move-result v1
#   invoke-virtual {v0, v1}, ...fromType(I)...SongSortType;
#   move-result-object v0
#   .line 108
#   iget-object v1, p0, ArtistsActivity;->artist:Ljava/lang/String;
#   invoke-direct {p0, v1, v0}, ...switchSongSortType(String SongSortType)V
#   .line 109
#   invoke-virtual {p0}, ...getVb()...
#   move-result-object v0
#   check-cast v0, ActivityArtistsBinding;
#   iget-object v0, v0, ActivityArtistsBinding;->spv ...
#   invoke-virtual {v0}, ShufflePlaylistItemView;->show()V
#   goto :goto_1
#
# We replace this entire block (lines 107-109 + goto) with an Intent
# to AlbumsActivity. The artist name is already in p0.artist at this point.
#
# Register usage (registers_size=5, p0=this=v4 in Dalvik calling convention,
# but apktool smali uses p0 notation for parameters):
#   v0 = new Intent instance
#   v1 = Context (from getContext())
#   v2 = Class literal (AlbumsActivity.class) / artist string
#   p0 = this

artists_path = os.path.join(UNPACKED_DIR, ARTISTS_SMALI)
if not os.path.exists(artists_path):
    sys.exit(f"ERROR: Expected smali not found: {artists_path}")

with open(artists_path, 'r') as f:
    artists_src = f.read()

OLD_ARTISTS = """\
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

NEW_ARTISTS = (
    "    .line 108\n"
    "\n"
    "    new-instance v0, Landroid/content/Intent;\n"
    "\n"
    "    invoke-virtual {p0}, Lcom/innioasis/music/ArtistsActivity;->getContext()Landroid/content/Context;\n"
    "\n"
    "    move-result-object v1\n"
    "\n"
    "    const-class v2, Lcom/innioasis/music/AlbumsActivity;\n"
    "\n"
    "    invoke-direct {v0, v1, v2}, Landroid/content/Intent;-><init>(Landroid/content/Context;Ljava/lang/Class;)V\n"
    "\n"
    f"    const-string v1, \"{ARTIST_INTENT_KEY}\"\n"
    "\n"
    "    iget-object v2, p0, Lcom/innioasis/music/ArtistsActivity;->artist:Ljava/lang/String;\n"
    "\n"
    "    invoke-virtual {v0, v1, v2}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;\n"
    "\n"
    "    invoke-virtual {p0, v0}, Lcom/innioasis/music/ArtistsActivity;->startActivity(Landroid/content/Intent;)V\n"
    "\n"
    "    goto :goto_1"
)

if OLD_ARTISTS not in artists_src:
    sys.exit(
        "ERROR: ArtistsActivity patch target not found.\n"
        "  The smali structure may differ from 3.0.2.\n"
        "  Inspect ArtistsActivity.smali and locate the switchSongSortType\n"
        "  call in the confirm() method's artist-tap branch."
    )

artists_src = artists_src.replace(OLD_ARTISTS, NEW_ARTISTS, 1)
with open(artists_path, 'w') as f:
    f.write(artists_src)
print(f"  Patch A: ArtistsActivity -- artist tap now launches AlbumsActivity with {ARTIST_INTENT_KEY!r}")

# ============================================================
# Patch B: AlbumsActivity.smali
# ============================================================
#
# initView() verified bytecode layout (registers_size=3, p0=this):
#   v0, v1: scratch registers (locals=2)
#   Instr 0:  const v0, 2131820833
#   Instrs 1-5:  getString + setStateBarLeftText (title setup)
#   Instrs 6-13: getVb -> ListView.setAdapter(AlbumListAdapter)
#   Instrs 14-21: getVb -> SPV.bind(SongListAdapter)
#   Instrs 22-29: SortAlbumType.fromType -> getAlbumListBySort -> return-void
#
# We replace the entire method, increasing .locals from 2 to 4 to accommodate
# the artist-filter branch (needs v0..v3; p0=this remains as p0 in smali).
#
# New block (inserted between instrs 21 and 22):
#   getIntent().getStringExtra("artist_key") -> v0
#   if null or empty -> :cond_no_artist (original sort flow)
#   Y1Repository.getAlbumsByKey(v0) -> v2
#   AlbumListAdapter.setAlbums(v2)
#   return-void
#   :cond_no_artist -> original sort flow -> return-void

albums_path = os.path.join(UNPACKED_DIR, ALBUMS_SMALI)
if not os.path.exists(albums_path):
    sys.exit(f"ERROR: Expected smali not found: {albums_path}")

with open(albums_path, 'r') as f:
    albums_src = f.read()

# Match the complete initView() method body. The resource ID constant
# is captured so it can be preserved verbatim in the replacement.
INIT_VIEW_PATTERN = re.compile(
    r'(\.method public initView\(\)V\n'
    r'    \.locals 2\n'
    r'\n'
    r'    )(const v0, (?:0x[0-9a-fA-F]+|\d+))'
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
    sys.exit(
        "ERROR: AlbumsActivity initView() pattern not found.\n"
        "  The smali structure may differ from 3.0.2.\n"
        "  Inspect AlbumsActivity.smali manually."
    )

res_id_instr = m.group(2)  # e.g. "const v0, 0x7f110121" (apktool writes hex for large constants)
print(f"  Detected initView resource ID: {res_id_instr}")

NEW_INIT_VIEW = (
    ".method public initView()V\n"
    "    .locals 8\n"
    "\n"
    f"    {res_id_instr}\n"
    "\n"
    "    .line 50\n"
    "    invoke-virtual {p0, v0}, Lcom/innioasis/music/AlbumsActivity;->getString(I)Ljava/lang/String;\n"
    "\n"
    "    move-result-object v0\n"
    "\n"
    "    const-string v1, \"getString(R.string.music_albums)\"\n"
    "\n"
    "    invoke-static {v0, v1}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullExpressionValue(Ljava/lang/Object;Ljava/lang/String;)V\n"
    "\n"
    "    invoke-virtual {p0, v0}, Lcom/innioasis/music/AlbumsActivity;->setStateBarLeftText(Ljava/lang/String;)V\n"
    "\n"
    "    .line 51\n"
    "    invoke-virtual {p0}, Lcom/innioasis/music/AlbumsActivity;->getVb()Landroidx/viewbinding/ViewBinding;\n"
    "\n"
    "    move-result-object v0\n"
    "\n"
    "    check-cast v0, Lcom/innioasis/y1/databinding/ActivityAlbumsBinding;\n"
    "\n"
    "    iget-object v0, v0, Lcom/innioasis/y1/databinding/ActivityAlbumsBinding;->lv:Landroid/widget/ListView;\n"
    "\n"
    "    invoke-direct {p0}, Lcom/innioasis/music/AlbumsActivity;->getAdapter()Lcom/innioasis/music/adapter/AlbumListAdapter;\n"
    "\n"
    "    move-result-object v1\n"
    "\n"
    "    check-cast v1, Landroid/widget/ListAdapter;\n"
    "\n"
    "    invoke-virtual {v0, v1}, Landroid/widget/ListView;->setAdapter(Landroid/widget/ListAdapter;)V\n"
    "\n"
    "    .line 52\n"
    "    invoke-virtual {p0}, Lcom/innioasis/music/AlbumsActivity;->getVb()Landroidx/viewbinding/ViewBinding;\n"
    "\n"
    "    move-result-object v0\n"
    "\n"
    "    check-cast v0, Lcom/innioasis/y1/databinding/ActivityAlbumsBinding;\n"
    "\n"
    "    iget-object v0, v0, Lcom/innioasis/y1/databinding/ActivityAlbumsBinding;->spv:Lcom/innioasis/y1/view/ShufflePlaylistItemView;\n"
    "\n"
    "    invoke-direct {p0}, Lcom/innioasis/music/AlbumsActivity;->getSongAdapter()Lcom/innioasis/music/adapter/SongListAdapter;\n"
    "\n"
    "    move-result-object v1\n"
    "\n"
    "    check-cast v1, Lcom/innioasis/music/adapter/MyBaseAdapter;\n"
    "\n"
    "    invoke-virtual {v0, v1}, Lcom/innioasis/y1/view/ShufflePlaylistItemView;->bind(Lcom/innioasis/music/adapter/MyBaseAdapter;)V\n"
    "\n"
    "    .line 53\n"
    "\n"
    "    invoke-virtual {p0}, Lcom/innioasis/music/AlbumsActivity;->getIntent()Landroid/content/Intent;\n"
    "\n"
    "    move-result-object v0\n"
    "\n"
    f"    const-string v1, \"{ARTIST_INTENT_KEY}\"\n"
    "\n"
    "    invoke-virtual {v0, v1}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;\n"
    "\n"
    "    move-result-object v0\n"
    "\n"
    "    if-eqz v0, :cond_no_artist\n"
    "\n"
    "    invoke-virtual {v0}, Ljava/lang/String;->isEmpty()Z\n"
    "\n"
    "    move-result v1\n"
    "\n"
    "    if-nez v1, :cond_no_artist\n"
    "\n"
    "    # Get Y1Repository -> SongDao -> call getSongsByArtistSortByAlbum(artist)\n"
    "    # Returns List<Song> ordered by pinyinAlbum. We deduplicate by album name\n"
    "    # into an ordered ArrayList<String>, then pass to setAlbums().\n"
    "    # Registers: v0=artist, v1=repo, v2=songDao, v3=songs iterator,\n"
    "    #            v4=result ArrayList, v5=seen LinkedHashSet,\n"
    "    #            v6=current Song / album String, v7=scratch\n"
    "\n"
    "    sget-object v1, Lcom/innioasis/y1/Y1Application;->Companion:Lcom/innioasis/y1/Y1Application$Companion;\n"
    "\n"
    "    invoke-virtual {v1}, Lcom/innioasis/y1/Y1Application$Companion;->getY1Repository()Lcom/innioasis/y1/database/Y1Repository;\n"
    "\n"
    "    move-result-object v1\n"
    "\n"
    "    # songDao field is made public by Patch C (Y1Repository.smali) so iget-object works.\n"
    "    iget-object v2, v1, Lcom/innioasis/y1/database/Y1Repository;->songDao:Lcom/innioasis/y1/database/SongDao;\n"
    "\n"
    "    invoke-interface {v2, v0}, Lcom/innioasis/y1/database/SongDao;->getSongsByArtistSortByAlbum(Ljava/lang/String;)Ljava/util/List;\n"
    "\n"
    "    move-result-object v3\n"
    "\n"
    "    new-instance v4, Ljava/util/ArrayList;\n"
    "    invoke-direct {v4}, Ljava/util/ArrayList;-><init>()V\n"
    "\n"
    "    new-instance v5, Ljava/util/LinkedHashSet;\n"
    "    invoke-direct {v5}, Ljava/util/LinkedHashSet;-><init>()V\n"
    "\n"
    "    invoke-interface {v3}, Ljava/util/List;->iterator()Ljava/util/Iterator;\n"
    "    move-result-object v3\n"
    "\n"
    "    :loop_songs\n"
    "    invoke-interface {v3}, Ljava/util/Iterator;->hasNext()Z\n"
    "    move-result v7\n"
    "    if-eqz v7, :loop_done\n"
    "\n"
    "    invoke-interface {v3}, Ljava/util/Iterator;->next()Ljava/lang/Object;\n"
    "    move-result-object v6\n"
    "    check-cast v6, Lcom/innioasis/y1/database/Song;\n"
    "\n"
    "    invoke-virtual {v6}, Lcom/innioasis/y1/database/Song;->getAlbum()Ljava/lang/String;\n"
    "    move-result-object v6\n"
    "\n"
    "    if-eqz v6, :loop_songs\n"
    "\n"
    "    invoke-virtual {v5, v6}, Ljava/util/LinkedHashSet;->add(Ljava/lang/Object;)Z\n"
    "    move-result v7\n"
    "    if-eqz v7, :loop_songs\n"
    "\n"
    "    invoke-interface {v4, v6}, Ljava/util/List;->add(Ljava/lang/Object;)Z\n"
    "    goto :loop_songs\n"
    "\n"
    "    :loop_done\n"
    "    invoke-direct {p0}, Lcom/innioasis/music/AlbumsActivity;->getAdapter()Lcom/innioasis/music/adapter/AlbumListAdapter;\n"
    "    move-result-object v3\n"
    "\n"
    "    invoke-virtual {v3, v4}, Lcom/innioasis/music/adapter/AlbumListAdapter;->setAlbums(Ljava/util/List;)V\n"
    "\n"
    "    return-void\n"
    "\n"
    "    :cond_no_artist\n"
    "    sget-object v0, Lcom/innioasis/y1/database/Y1Repository$SortAlbumType;->Companion:Lcom/innioasis/y1/database/Y1Repository$SortAlbumType$Companion;\n"
    "\n"
    "    sget-object v1, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->INSTANCE:Lcom/innioasis/y1/utils/SharedPreferencesUtils;\n"
    "\n"
    "    invoke-virtual {v1}, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->getSortAlbum()I\n"
    "\n"
    "    move-result v1\n"
    "\n"
    "    invoke-virtual {v0, v1}, Lcom/innioasis/y1/database/Y1Repository$SortAlbumType$Companion;->fromType(I)Lcom/innioasis/y1/database/Y1Repository$SortAlbumType;\n"
    "\n"
    "    move-result-object v0\n"
    "\n"
    "    invoke-direct {p0, v0}, Lcom/innioasis/music/AlbumsActivity;->getAlbumListBySort(Lcom/innioasis/y1/database/Y1Repository$SortAlbumType;)V\n"
    "\n"
    "    return-void\n"
    ".end method"
)

albums_src = INIT_VIEW_PATTERN.sub(NEW_INIT_VIEW, albums_src, count=1)
with open(albums_path, 'w') as f:
    f.write(albums_src)
print(f"  Patch B: AlbumsActivity -- initView reads {ARTIST_INTENT_KEY!r} and filters albums")

# ============================================================
# Patch C: Y1Repository.smali -- make songDao field public
# ============================================================
#
# Y1Repository.songDao is declared `private final` (access_flags=0x12).
# AlbumsActivity (in a different package) cannot access it via iget-object:
# Dalvik's verifier throws IllegalAccessError at class load time.
#
# The Kotlin-generated accessor access$getSongDao$p exists but exhibits
# unreliable NoSuchMethodError behaviour on this device's old Dalvik (API 17).
#
# Simplest fix: change the field to `public final` (access_flags=0x11).
# The field is internal to a private system app, so no security implication.
# apktool writes the declaration as:
#   .field private final songDao:Lcom/innioasis/y1/database/SongDao;
# We change it to:
#   .field public final songDao:Lcom/innioasis/y1/database/SongDao;

repo_path = os.path.join(UNPACKED_DIR, REPO_SMALI)
if not os.path.exists(repo_path):
    sys.exit(f"ERROR: Expected smali not found: {repo_path}")

with open(repo_path, 'r') as f:
    repo_src = f.read()

OLD_FIELD = ".field private final songDao:Lcom/innioasis/y1/database/SongDao;"
NEW_FIELD = ".field public final songDao:Lcom/innioasis/y1/database/SongDao;"

if OLD_FIELD not in repo_src:
    sys.exit(
        "ERROR: Y1Repository songDao field declaration not found.\n"
        f"  Expected: {OLD_FIELD}\n"
        "  Inspect Y1Repository.smali manually."
    )

repo_src = repo_src.replace(OLD_FIELD, NEW_FIELD, 1)
with open(repo_path, 'w') as f:
    f.write(repo_src)
print("  Patch C: Y1Repository -- songDao field changed from private to public")

# -- Step 4: Reassemble DEX with apktool -------------------------------------
print(f"\n[4/4] Reassembling smali -> DEX (this takes ~30 seconds)...")
# apktool builds smali->DEX first, then tries aapt for resources.
# Since we decoded with --no-res, the aapt step fails -- but the DEX
# is already built by that point. We ignore the exit code intentionally.
subprocess.run(
    [java, "-jar", APKTOOL_JAR, "b", UNPACKED_DIR],
    capture_output=True, text=True
)

dex1 = os.path.join(UNPACKED_DIR, "build", "apk", "classes.dex")
dex2 = os.path.join(UNPACKED_DIR, "build", "apk", "classes2.dex")
if not os.path.exists(dex1) or not os.path.exists(dex2):
    sys.exit("ERROR: DEX assembly failed -- classes.dex or classes2.dex not produced.")
print(f"  classes.dex  {os.path.getsize(dex1):,} bytes")
print(f"  classes2.dex {os.path.getsize(dex2):,} bytes")

# -- Build patched APK (replace DEX, keep original META-INF) -----------------
with open(dex1, 'rb') as f: dex1_bytes = f.read()
with open(dex2, 'rb') as f: dex2_bytes = f.read()

with zipfile.ZipFile(ORIGINAL_APK, 'r') as zin:
    with zipfile.ZipFile(OUTPUT_APK, 'w',
                         compression=zipfile.ZIP_DEFLATED,
                         allowZip64=True) as zout:
        for item in zin.infolist():
            if item.filename == 'classes.dex':
                zout.writestr(item, dex1_bytes)
            elif item.filename == 'classes2.dex':
                zout.writestr(item, dex2_bytes)
            else:
                zout.writestr(item, zin.read(item.filename))  # includes META-INF/

size = os.path.getsize(OUTPUT_APK)
print(f"  Patched APK: {OUTPUT_APK} ({size:,} bytes)")

# -- Done --------------------------------------------------------------------
print(f"""
{'=' * 60}
SUCCESS
{'=' * 60}
Output:  {OUTPUT_APK}

Deploy via ADB push (requires root / remounted /system):
  adb root
  adb remount
  adb push {OUTPUT_APK} /system/app/com.innioasis.y1/com.innioasis.y1.apk
  adb shell chmod 644 /system/app/com.innioasis.y1/com.innioasis.y1.apk
  adb reboot

Do NOT use `adb install` -- PackageManager will reject the APK
due to signature mismatch (com.innioasis.y1 is a system app).
{'=' * 60}
""")

shutil.rmtree(WORK_DIR, ignore_errors=True)
