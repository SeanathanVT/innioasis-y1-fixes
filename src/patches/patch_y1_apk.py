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
  Java 11–21  (apktool 2.9.3's smali assembler is unreliable on Java 22+;
               see WHAT THIS PATCH DOES below for the silent-drop failure
               mode, and the Java-version warning printed at startup).
  apktool 2.9.3 (downloaded automatically into `tools/` if not present;
               md5-verified against e28e4b4a413a252617d92b657a33c947).
  pip packages: androguard
    pip install androguard

USAGE
-----
  python3 patch_y1_apk.py <path/to/com_innioasis_y1_X_X_X.apk>
  python3 patch_y1_apk.py [--skip-md5] [--clean-staging] <apk>

  If no argument is given, the script looks for any
  com_innioasis_y1_*.apk in the current directory.

  --skip-md5     bypasses the input APK md5 check (the patcher pins to
                 stock 3.0.2 by default; an already-patched APK fed back
                 in would silently fail to apply the patches without it).
  --clean-staging wipes the cached staging dir before patching (default
                 reuses it, which is faster across iterations).

  apktool jar is downloaded once and cached in `tools/` at the repo root.
  Decoded smali + rebuilt DEX live under `staging/y1-apk/` and are
  retained between runs for inspection.

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
  Four smali patches (A/B/C for Artist→Album navigation, E for discrete
  PASSTHROUGH PLAY/PAUSE coverage), no new files, no Manifest changes.

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
import argparse, hashlib
import glob
import logging
from collections import Counter

# Silence androguard's logging upfront, before any \`from androguard…\` import
# runs. Two channels matter: the stdlib logger (androguard 3.x) and loguru
# (androguard 4.x switched to it; ignores the stdlib config). loguru is only
# imported here if androguard pulled it in as a transitive dep.
logging.getLogger("androguard").setLevel(logging.ERROR)
try:
    from loguru import logger as _loguru
    _loguru.disable("androguard")
except ImportError:
    pass

# -- Config -------------------------------------------------------------------
# Repo-rooted paths so the patcher works the same regardless of CWD. The
# downloaded apktool jar and the decoded/rebuilt smali tree are both retained
# across runs (`tools/` and `staging/y1-apk/` respectively) so iterative
# testing doesn't pay the apktool-download + APK-decode cost every time.
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))           # src/patches
REPO_ROOT   = os.path.dirname(os.path.dirname(SCRIPT_DIR))         # repo root
TOOLS_DIR   = os.path.join(REPO_ROOT, "tools")
STAGING_DIR = os.path.join(REPO_ROOT, "staging", "y1-apk")
UNPACKED_DIR = os.path.join(STAGING_DIR, "unpacked")

APKTOOL_VERSION = "2.9.3"
APKTOOL_JAR     = os.path.join(TOOLS_DIR, f"apktool-{APKTOOL_VERSION}.jar")
APKTOOL_URL     = f"https://github.com/iBotPeaches/Apktool/releases/download/v{APKTOOL_VERSION}/apktool_{APKTOOL_VERSION}.jar"
APKTOOL_MD5     = "e28e4b4a413a252617d92b657a33c947"  # apktool 2.9.3

# Why apktool 2.9.3 and not a newer release:
#   - apktool 2.10.x / 2.11.x / 2.12.x / 3.0.x have all changed the `b`
#     workflow to write DEXes only into a final dist/<name>.apk rather than
#     leaving them in build/apk/ when aapt fails (which is what we exploited
#     with --no-res to skip resource processing). Each new release would
#     require reworking the patcher's DEX-extraction step.
#   - apktool 2.9.3's bundled smali assembler (smali 2.5.x, baksmali 2.5.x)
#     does NOT support Java 22+ JVMs reliably — historical observation:
#     against Java 25, it silently dropped one of iter21's two FF/RW lambda
#     edits during DEX assembly while preserving the other. iter21 was
#     reverted in iter24 (kernel-level fix in `patch_libextavrcp_jni.py`'s
#     U1 patch closed the AVRCP-side root cause it was guarding against),
#     so this is no longer load-bearing. Java 22+ may still be problematic
#     for unrelated reasons; pin to 11–21 if you hit assembler weirdness.
#
# Practical recommendation: run the patcher under Java 11–21. If your flash
# box is on Java 22+, install OpenJDK 21 alongside (Debian/Ubuntu:
# `apt install openjdk-21-jdk` and either `update-alternatives --config java`
# or invoke /usr/lib/jvm/java-21-openjdk-*/bin/java directly).

# apktool 2.9.3's smali assembler is memory-frugal and runs fine at the
# default JVM heap on this APK. Newer apktool releases (2.10+) use a parallel
# ThreadPoolExecutor that may need `-Xmx2g` for large APKs; keeping this
# slot here so a future bump can wire it up by changing one constant.
APKTOOL_JVM_FLAGS: list = []

# Stock APK md5 — pulled from /system/app/com.innioasis.y1/ on a clean v3.0.2
# device. The smali pattern matches in this script assume unpatched bytecode,
# so re-running against an already-patched APK silently fails to apply the
# patches. The md5 check rejects any non-stock input by default; pass
# --skip-md5 to override (diagnostic use only).
STOCK_APK_MD5 = "d2cd2841305830db2daf388cb9866c67"

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


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_input_apk(path: str, skip_md5: bool) -> None:
    """Pin input to the stock 3.0.2 APK so we don't silently re-patch."""
    actual = md5_file(path)
    if actual == STOCK_APK_MD5:
        print(f"  Input md5: {actual}  (stock 3.0.2, verified)")
        return
    msg = (
        f"\nERROR: input APK md5 mismatch.\n"
        f"  Expected: {STOCK_APK_MD5}  (stock com.innioasis.y1_3.0.2.apk)\n"
        f"  Got:      {actual}\n"
        f"\n"
        f"  This patcher operates only on the stock APK pulled from\n"
        f"  /system/app/com.innioasis.y1/ on a clean v3.0.2 device. The\n"
        f"  smali pattern matches assume unpatched bytecode -- patching an\n"
        f"  already-patched APK silently fails to apply the patches.\n"
        f"\n"
        f"  Recover a stock APK with:\n"
        f"    adb pull /system/app/com.innioasis.y1/com.innioasis.y1.apk\n"
        f"\n"
        f"  --skip-md5 bypasses this check (diagnostic use only).\n"
    )
    if skip_md5:
        print(f"  WARNING: input md5 {actual} != expected {STOCK_APK_MD5} (--skip-md5 set, proceeding)")
        return
    sys.exit(msg)


def ensure_apktool() -> None:
    """Resolve apktool jar in `tools/`, downloading + md5-verifying if needed."""
    os.makedirs(TOOLS_DIR, exist_ok=True)
    cached = (
        os.path.exists(APKTOOL_JAR)
        and os.path.getsize(APKTOOL_JAR) > 1_000_000
    )
    if cached:
        actual = md5_file(APKTOOL_JAR)
        if actual == APKTOOL_MD5:
            print(f"  apktool {APKTOOL_VERSION}: cached at {APKTOOL_JAR} (md5 verified)")
            return
        print(f"  apktool {APKTOOL_VERSION}: cached but md5 mismatch ({actual}); re-downloading")
        os.remove(APKTOOL_JAR)
    print(f"  apktool {APKTOOL_VERSION}: downloading from {APKTOOL_URL} ...")
    try:
        urllib.request.urlretrieve(APKTOOL_URL, APKTOOL_JAR)
    except Exception as e:
        sys.exit(
            f"ERROR downloading apktool: {e}\n"
            f"  Manual fix: download {APKTOOL_URL}\n"
            f"  and place at {APKTOOL_JAR} (must match md5 {APKTOOL_MD5})."
        )
    actual = md5_file(APKTOOL_JAR)
    if actual != APKTOOL_MD5:
        os.remove(APKTOOL_JAR)
        sys.exit(
            f"ERROR: downloaded apktool md5 mismatch.\n"
            f"  Expected: {APKTOOL_MD5}\n"
            f"  Got:      {actual}\n"
            f"  Removed the bad download; re-run to retry."
        )
    print(f"  apktool {APKTOOL_VERSION}: saved to {APKTOOL_JAR} ({os.path.getsize(APKTOOL_JAR):,} bytes, md5 verified)")


def get_apk_info(apk_path: str):
    """Extract package name and version from binary AndroidManifest.xml."""
    try:
        # Re-apply loguru disable here too — if androguard wasn't yet imported
        # at module-load time, it's about to be imported now and we need the
        # filter in place before the first log emission.
        try:
            from loguru import logger as _loguru
            _loguru.disable("androguard")
        except ImportError:
            pass
        from androguard.core.apk import APK
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
parser = argparse.ArgumentParser(
    description="Innioasis Y1 com.innioasis.y1 APK smali patcher (Artist→Album + discrete PASSTHROUGH PLAY/PAUSE).",
    epilog="See the docstring at the top of this script for the full per-patch detail."
)
parser.add_argument(
    'apk', nargs='?',
    help='Path to stock com.innioasis.y1_3.0.2.apk. If omitted, looks for one in CWD.'
)
parser.add_argument(
    '--skip-md5', action='store_true',
    help=f'Bypass input APK md5 check (expected: {STOCK_APK_MD5}). Diagnostic use only.'
)
parser.add_argument(
    '--clean-staging', action='store_true',
    help=f'Wipe {STAGING_DIR} before patching. Default reuses the decoded smali tree.'
)
args = parser.parse_args()

print("=" * 60)
print("Innioasis Y1 com.innioasis.y1 APK patcher")
print("=" * 60)

if args.apk:
    ORIGINAL_APK = args.apk
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

verify_input_apk(ORIGINAL_APK, args.skip_md5)

pkg_name, version = get_apk_info(ORIGINAL_APK)
os.makedirs("output", exist_ok=True)
OUTPUT_APK = os.path.join("output", f"{pkg_name}_{version}-patched.apk")
print(f"  Package:  {pkg_name}")
print(f"  Version:  {version}")
print(f"  Output:   {OUTPUT_APK}")
print(f"  Staging:  {STAGING_DIR}")

java = find_java()
print(f"  Java:     {java}")

# JVM version detection. apktool 2.9.3's bundled smali assembler is
# JVM-version-sensitive on Java 22+ -- historical observation: Java 25
# silently dropped one of iter21's two FF/RW lambda edits during DEX
# reassembly while preserving the other. iter21 was reverted in iter24
# (kernel-level fix in patch_libextavrcp_jni.py's U1 patch), so we no
# longer have a known smali pattern that triggers this. Warn anyway --
# Java 22+ may still have other compat issues with apktool 2.9.3's smali.
try:
    java_ver_proc = subprocess.run([java, "--version"], capture_output=True, text=True)
    java_ver_str = (java_ver_proc.stdout or java_ver_proc.stderr).strip().splitlines()
    if java_ver_str:
        print(f"  JVM:      {java_ver_str[0]}")
        m = re.search(r'(?:openjdk|java)\s+(\d+)', java_ver_str[0].lower())
        if m and int(m.group(1)) >= 22:
            print(
                f"  WARNING: Java {m.group(1)} detected. apktool {APKTOOL_VERSION}'s\n"
                f"           bundled smali assembler has been observed to silently drop\n"
                f"           patches during DEX reassembly on Java 22+. If your patched\n"
                f"           APK behaves unexpectedly, install Java 21 and re-run with\n"
                f"           that JVM (Debian/Ubuntu: `apt install openjdk-21-jdk`, then\n"
                f"           invoke /usr/lib/jvm/java-21-openjdk-*/bin/java directly or\n"
                f"           `update-alternatives --config java`)."
            )
except Exception:
    pass

# -- Step 1: Locate or download apktool ---------------------------------------
print(f"\n[1/4] Resolving apktool {APKTOOL_VERSION}...")
ensure_apktool()

# -- Step 2: Unpack APK -------------------------------------------------------
print(f"\n[2/4] Unpacking APK with apktool...")
os.makedirs(STAGING_DIR, exist_ok=True)
if args.clean_staging and os.path.exists(STAGING_DIR):
    print(f"      --clean-staging: wiping {STAGING_DIR}")
    shutil.rmtree(STAGING_DIR)
    os.makedirs(STAGING_DIR, exist_ok=True)
if os.path.exists(UNPACKED_DIR):
    shutil.rmtree(UNPACKED_DIR)
run([java, *APKTOOL_JVM_FLAGS, "-jar", APKTOOL_JAR, "d", "--no-res", "-f",
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

# ============================================================
# Patch E: PlayControllerReceiver.smali — discrete PLAY/PAUSE coverage  (iter22d, refined iter25)
# ============================================================
#
# Background
# ----------
# AVRCP 1.3 §4.6.1 (PASS THROUGH command — actual op-code table and
# press/release semantics defined in AV/C Panel Subunit Specification, ref
# [2] of AVRCP 1.3) gives distinct codes for PLAY (0x44) and PAUSE (0x46),
# separate from any toggle abstraction. AVRCP 1.3 §19.3 (Appendix D,
# informative) shows a concrete PASSTHROUGH PLAY frame with operation_ID
# 0x44 and confirms `state_flag = 0` (press) / `1` (release). CTs that
# issue both discrete codes from separate UI elements are spec-conformant;
# CTs that only ever issue 0x46 (and rely on the TG to interpret it as a
# toggle when already paused) are also common in practice. A spec-compliant
# TG must therefore handle all three of:
#   - 0x44 PLAY  : transition to PLAYING from any state (no-op if already PLAYING)
#   - 0x46 PAUSE : transition to PAUSED  from any state (no-op if already PAUSED)
#   - 0x46 sent as a toggle by the CT: TG state-flip
#
# Key-injection path inside libextavrcp_jni.so (`avrcp_input_sendkey` →
# /dev/uinput) maps these to the Linux input event keycodes (verified against
# /system/usr/keylayout/AVRCP.kl + getevent capture on iter23 hardware):
#   - 0x44 PLAY  → Linux KEY_PLAYCD (200)  → Android KEYCODE_MEDIA_PLAY (126)
#   - 0x46 PAUSE → Linux KEY_PAUSECD (201) → Android KEYCODE_MEDIA_PLAY_PAUSE (85)
# (PASSTHROUGH 0x45 STOP is also defined but doesn't matter for this patch.)
#
# Y1's PlayControllerReceiver (the registered ACTION_MEDIA_BUTTON receiver)
# stock-only matches against `KeyMap.KEY_PLAY` (= 85, KEYCODE_MEDIA_PLAY_PAUSE).
# When a CT issues a discrete PLAY (PASSTHROUGH 0x44 → uinput KEY_PLAYCD →
# KEYCODE_MEDIA_PLAY 126), the receiver's `if-ne v2, KEY_PLAY` check fails,
# no action runs, and the music app silently drops the command.
#
# The fix
# -------
# Distinguish three cases in the receiver and route each to the *correct*
# PlayerService method:
#
#   - KEY_PLAY (85, KEYCODE_MEDIA_PLAY_PAUSE):
#       call `playOrPause()V` (toggle).
#       The legacy ACTION_MEDIA_BUTTON broadcast Intent always uses 85, and
#       toggle is the right semantics for a single physical play/pause key.
#
#   - KEYCODE_MEDIA_PLAY (0x7e, 126) — discrete PLAY:
#       call `play(Z)V` with bool=false. AVRCP 1.3 §4.6.1 / AV/C Panel
#       Subunit Spec — PLAY (op_id 0x44) transitions to
#       PLAYING from any state; if already PLAYING, play() is effectively a
#       no-op (the underlying IjkMediaPlayer is already running).
#
#   - KEYCODE_MEDIA_PAUSE (0x7f, 127) — discrete PAUSE:
#       call `pause(IZ)V` with reason=0x12, flag=true. The reason byte is a
#       diagnostic identifier that PlayerService Timber-logs as "executed
#       pause from %d"; existing reasons in the stock binary span 0xc-0x11
#       (e.g. 0xc/0xf/0xd/0xe = various internal sources, 0x10/0x11 from
#       playOrPause's pause-arm). 0x12 is a fresh tag for "PlayController
#       discrete PASSTHROUGH PAUSE". The boolean flag virtually always
#       resolves to true in the stock binary (Kotlin pause$default helper's
#       mask defaults p2=true on every observed callsite); we pass true
#       explicitly to match.
#
# iter22d's first attempt routed all three keycodes to playOrPause()
# (toggle). That was empirically wrong for a strict CT: dual-bolt-iter23
# capture showed Bolt issuing 5 discrete PLAY (0x44) presses while Y1 was
# already PLAYING — playOrPause() toggled to PAUSED on each press, the
# opposite of what Bolt asked for, and Bolt's UI reported the button as
# unresponsive. iter25 splits the join label into three discrete arms.
#
# Stock smali at PlayControllerReceiver.smali:cond_c:
#   :cond_c
#   sget-object p1, KeyMap;->INSTANCE
#   invoke-virtual {p1}, KeyMap;->getKEY_PLAY()I
#   move-result p1
#   if-ne v2, p1, :cond_e
#   ... (playOrPause action — single arm)
#
# Patched (iter25):
#   :cond_c
#   sget-object p1, KeyMap;->INSTANCE
#   invoke-virtual {p1}, KeyMap;->getKEY_PLAY()I
#   move-result p1
#   if-eq v2, p1, :cond_play_pause_toggle    # KEY_PLAY (85) → toggle
#   const/16 p1, 0x7e
#   if-eq v2, p1, :cond_play_strict          # MEDIA_PLAY (126) → play()
#   const/16 p1, 0x7f
#   if-eq v2, p1, :cond_pause_strict         # MEDIA_PAUSE (127) → pause()
#   goto :cond_e                             # nothing matched, keep walking
#
#   :cond_play_pause_toggle
#   ... (playOrPause action — unchanged from stock)
#   goto :goto_5
#
#   :cond_play_strict
#   sget-object p1, Y1Application$Companion
#   invoke-virtual {p1}, ->getPlayerService()...
#   move-result-object p1
#   if-eqz p1, :cond_e
#   const/4 v0, 0x0
#   invoke-virtual {p1, v0}, PlayerService;->play(Z)V
#   goto :goto_5
#
#   :cond_pause_strict
#   sget-object p1, Y1Application$Companion
#   invoke-virtual {p1}, ->getPlayerService()...
#   move-result-object p1
#   if-eqz p1, :cond_e
#   const/16 v0, 0x12
#   const/4 v3, 0x1
#   invoke-virtual {p1, v0, v3}, PlayerService;->pause(IZ)V
#   goto :goto_5
#
# v0 and v3 are dead at this point (the .locals 6 onReceive method has
# v0..v5; the keyCode lives in v2 throughout the dispatch chain; v0/v3
# are scratch). Each arm is a tail call ending in `goto :goto_5`, so we
# never fall through into the next label. apktool will renumber the
# user-defined :cond_play_pause_toggle / :cond_play_strict / :cond_pause_strict
# labels to alphanumeric :cond_X on reassembly — that's expected and fine.

PLAY_CONTROLLER_RECEIVER_SMALI = (
    "smali_classes2/com/innioasis/y1/receiver/PlayControllerReceiver.smali"
)

play_receiver_path = os.path.join(UNPACKED_DIR, PLAY_CONTROLLER_RECEIVER_SMALI)
if not os.path.exists(play_receiver_path):
    sys.exit(f"ERROR: Expected smali not found: {play_receiver_path}")

with open(play_receiver_path, 'r') as f:
    play_receiver_src = f.read()

# Match the unique KEY_PLAY → playOrPause branch — the short-press handler
# at :cond_c. The receiver also has a long-press handler further down that
# calls `longClickPlayBtnToStop()` for a held KEY_PLAY; we leave that alone
# (held PLAY is unusual on a car HMI / TV remote, and the long-press → STOP
# semantics don't generalize to discrete PLAY vs PAUSE). Anchor the match
# on the `getKEY_PLAY()` invocation immediately before the `playOrPause()`
# call so we hit the right :cond_c and not the long-press handler below.
OLD_PLAY_BRANCH = """\
    sget-object p1, Lcom/innioasis/fm/configs/KeyMap;->INSTANCE:Lcom/innioasis/fm/configs/KeyMap;

    invoke-virtual {p1}, Lcom/innioasis/fm/configs/KeyMap;->getKEY_PLAY()I

    move-result p1

    if-ne v2, p1, :cond_e

    .line 92
    sget-object p1, Lcom/innioasis/y1/Y1Application;->Companion:Lcom/innioasis/y1/Y1Application$Companion;

    invoke-virtual {p1}, Lcom/innioasis/y1/Y1Application$Companion;->getPlayerService()Lcom/innioasis/y1/service/PlayerService;

    move-result-object p1

    if-eqz p1, :cond_e

    invoke-virtual {p1}, Lcom/innioasis/y1/service/PlayerService;->playOrPause()V

    goto :goto_5"""

NEW_PLAY_BRANCH = """\
    sget-object p1, Lcom/innioasis/fm/configs/KeyMap;->INSTANCE:Lcom/innioasis/fm/configs/KeyMap;

    invoke-virtual {p1}, Lcom/innioasis/fm/configs/KeyMap;->getKEY_PLAY()I

    move-result p1

    if-eq v2, p1, :cond_play_pause_toggle

    const/16 p1, 0x7e

    if-eq v2, p1, :cond_play_strict

    const/16 p1, 0x7f

    if-eq v2, p1, :cond_pause_strict

    goto :cond_e

    :cond_play_pause_toggle
    .line 92
    sget-object p1, Lcom/innioasis/y1/Y1Application;->Companion:Lcom/innioasis/y1/Y1Application$Companion;

    invoke-virtual {p1}, Lcom/innioasis/y1/Y1Application$Companion;->getPlayerService()Lcom/innioasis/y1/service/PlayerService;

    move-result-object p1

    if-eqz p1, :cond_e

    invoke-virtual {p1}, Lcom/innioasis/y1/service/PlayerService;->playOrPause()V

    goto :goto_5

    :cond_play_strict
    sget-object p1, Lcom/innioasis/y1/Y1Application;->Companion:Lcom/innioasis/y1/Y1Application$Companion;

    invoke-virtual {p1}, Lcom/innioasis/y1/Y1Application$Companion;->getPlayerService()Lcom/innioasis/y1/service/PlayerService;

    move-result-object p1

    if-eqz p1, :cond_e

    const/4 v0, 0x0

    invoke-virtual {p1, v0}, Lcom/innioasis/y1/service/PlayerService;->play(Z)V

    goto :goto_5

    :cond_pause_strict
    sget-object p1, Lcom/innioasis/y1/Y1Application;->Companion:Lcom/innioasis/y1/Y1Application$Companion;

    invoke-virtual {p1}, Lcom/innioasis/y1/Y1Application$Companion;->getPlayerService()Lcom/innioasis/y1/service/PlayerService;

    move-result-object p1

    if-eqz p1, :cond_e

    const/16 v0, 0x12

    const/4 v3, 0x1

    invoke-virtual {p1, v0, v3}, Lcom/innioasis/y1/service/PlayerService;->pause(IZ)V

    goto :goto_5"""

if OLD_PLAY_BRANCH not in play_receiver_src:
    sys.exit(
        "ERROR: PlayControllerReceiver KEY_PLAY → playOrPause branch not found.\n"
        f"  File: {play_receiver_path}\n"
        "  The smali shape may differ from 3.0.2."
    )

play_receiver_src = play_receiver_src.replace(OLD_PLAY_BRANCH, NEW_PLAY_BRANCH, 1)
with open(play_receiver_path, 'w') as f:
    f.write(play_receiver_src)
print(
    "  Patch E (iter25): PlayControllerReceiver -- KEY_PLAY (85) → playOrPause (toggle); "
    "KEYCODE_MEDIA_PLAY (126) → play(false) [discrete PLAY per AVRCP 1.3 §4.6.1 / AV/C Panel Subunit op 0x44]; "
    "KEYCODE_MEDIA_PAUSE (127) → pause(0x12, true) [discrete PAUSE per AVRCP 1.3 §4.6.1 / op 0x46]"
)

# -- Per-smali md5 report -----------------------------------------------------
# Hash each patched smali file. These hashes are deterministic regardless of
# Java version or apktool reassembly behavior, so they reliably indicate
# whether the smali edits succeeded.
print(f"\nPatched smali file md5s (deterministic — same across machines):")
PATCHED_SMALI_FILES = [
    ARTISTS_SMALI, ALBUMS_SMALI, REPO_SMALI,
    PLAY_CONTROLLER_RECEIVER_SMALI,
]
for rel in PATCHED_SMALI_FILES:
    full = os.path.join(UNPACKED_DIR, rel)
    if os.path.exists(full):
        print(f"  {rel}: {md5_file(full)}")
    else:
        print(f"  {rel}: MISSING")

# -- Step 4: Reassemble DEX with apktool -------------------------------------
print(f"\n[4/4] Reassembling smali -> DEX (this takes ~30 seconds)...")
# apktool builds smali->DEX first, then tries aapt for resources.
# Since we decoded with --no-res, the aapt step fails -- but the DEX
# is already built by that point. We ignore the exit code intentionally.
subprocess.run(
    [java, *APKTOOL_JVM_FLAGS, "-jar", APKTOOL_JAR, "b", UNPACKED_DIR],
    capture_output=True, text=True
)

dex1 = os.path.join(UNPACKED_DIR, "build", "apk", "classes.dex")
dex2 = os.path.join(UNPACKED_DIR, "build", "apk", "classes2.dex")
if not os.path.exists(dex1) or not os.path.exists(dex2):
    sys.exit("ERROR: DEX assembly failed -- classes.dex or classes2.dex not produced.")
print(f"  classes.dex  {os.path.getsize(dex1):,} bytes")
print(f"  classes2.dex {os.path.getsize(dex2):,} bytes")

with open(dex1, 'rb') as f: dex1_bytes = f.read()
with open(dex2, 'rb') as f: dex2_bytes = f.read()

# -- Build patched APK (replace DEX, keep original META-INF) -----------------
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

Retained artifacts:
  apktool jar:   {APKTOOL_JAR}
  staging dir:   {STAGING_DIR}/
    decoded smali:  {UNPACKED_DIR}/
    rebuilt DEX:    {os.path.join(UNPACKED_DIR, 'build', 'apk')}/

Re-run with --clean-staging for a fresh decode, or just re-run to reuse
the cached apktool jar and re-decode/patch incrementally.
{'=' * 60}
""")
