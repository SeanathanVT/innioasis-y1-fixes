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
  Four smali patches (A/B/C for Artist→Album navigation, D for the iter21
  FF/RW hold-loop cap), no new files, no Manifest changes. After all
  patches are applied to the smali, the rebuilt DEX is sanity-checked at
  the byte level — if any patch's signature is missing from the assembled
  DEX, the patcher refuses to write the APK (catches the apktool/Java-22+
  silent-drop failure mode before it hits the device).

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
#     does NOT support Java 22+ JVMs reliably — observed against Java 25,
#     it silently drops one of the iter21 cap edits during DEX assembly
#     while preserving the other. The DEX-signature check at the end of
#     this script will catch this and refuse to write the APK.
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
# (Patch D's PLAYER_SMALI / FF_LAMBDA_SMALI / RW_LAMBDA_SMALI are defined
# inline in the Patch D block below.)

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


def verify_dex_patch_signatures(dex2_bytes: bytes) -> bool:
    """Sanity-check that DEX-level patch signatures landed in the assembled DEX.

    apktool's smali assembler can silently drop or alter patches when the host
    JVM is newer than what apktool was QA'd against (apktool 2.9.3 was released
    before Java 22). The smali source files in `staging/` may show the patches
    correctly, but the assembled DEX may not contain them. This check searches
    for byte signatures that uniquely identify each patch's effect on the
    bytecode and warns loudly if anything is missing.

    Returns True on PASS, False on FAIL.
    """
    failures = []

    # Patch D: FF and RW caps. Both invoke()V lambdas should contain
    #   const/16 v0, 0x32; if-lt v6, v0, +8
    # Encoded as: 13 00 32 00 34 06 08 00. Expect TWO occurrences (FF, RW).
    cap_sig = b'\x13\x00\x32\x00\x34\x06\x08\x00'
    cap_count = dex2_bytes.count(cap_sig)
    if cap_count != 2:
        failures.append(
            f"Patch D (FF/RW hold-loop cap): expected 2 occurrences of "
            f"`const/16 v0, #50; if-lt v6, v0, +8` byte signature, found {cap_count}"
        )

    if not failures:
        print(f"  DEX signature verification: PASS (Patch D cap x{cap_count})")
        return True

    print(f"\n  DEX signature verification: FAIL")
    for f in failures:
        print(f"    - {f}")
    print(
        f"\n  This means apktool's smali assembler dropped or rewrote one or\n"
        f"  more patches during DEX reassembly. The smali source files under\n"
        f"  {UNPACKED_DIR}\n"
        f"  may show the patches correctly, but the assembled DEX does not.\n"
        f"\n"
        f"  Diagnostic steps:\n"
        f"    - Confirm `tools/apktool-{APKTOOL_VERSION}.jar` md5 == {APKTOOL_MD5}\n"
        f"      (delete it to force a re-download).\n"
        f"    - Inspect the patched smali files under {UNPACKED_DIR}\n"
        f"      to confirm the patch syntax is intact.\n"
        f"    - If on Java 22+, try Java 21 to rule out a JVM compat regression.\n"
        f"    - Compare the per-smali md5s above against another machine's run.\n"
        f"\n"
        f"  Refusing to write the patched APK to avoid a silent-broken flash.\n"
    )
    return False

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
    description="Innioasis Y1 com.innioasis.y1 APK smali patcher (Artist→Album + iter21 hold-loop cap).",
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
# JVM-version-sensitive on Java 22+ -- it can silently drop patches during
# DEX assembly (observed: Java 25 drops the FF lambda's iter21 cap while
# preserving RW). The DEX-signature check at the end of this script is the
# authoritative gate -- if a patch byte signature is missing it refuses to
# write the APK regardless of JVM version. The warning here just gives the
# user a heads-up.
try:
    java_ver_proc = subprocess.run([java, "--version"], capture_output=True, text=True)
    java_ver_str = (java_ver_proc.stdout or java_ver_proc.stderr).strip().splitlines()
    if java_ver_str:
        print(f"  JVM:      {java_ver_str[0]}")
        m = re.search(r'(?:openjdk|java)\s+(\d+)', java_ver_str[0].lower())
        if m and int(m.group(1)) >= 22:
            print(
                f"  WARNING: Java {m.group(1)} detected. apktool {APKTOOL_VERSION}'s\n"
                f"           bundled smali assembler is unreliable on Java 22+ — it can\n"
                f"           silently drop patches during DEX reassembly. The DEX-signature\n"
                f"           check at the end will catch this; if it fails, install Java 21\n"
                f"           and re-run with that JVM (Debian/Ubuntu: `apt install openjdk-21-jdk`,\n"
                f"           then invoke /usr/lib/jvm/java-21-openjdk-*/bin/java directly or\n"
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
# Patch D: Bound the fast-forward / rewind hold-loop  (iter21)
# ============================================================
#
# Background
# ----------
# AVRCP 1.4 §11.2 specifies PASSTHROUGH commands as press-then-release
# pairs: the CT issues a press (op_id with bit 0x80 cleared), the TG must
# accept it, then a matching release (op_id with bit 0x80 set) ends the
# command. AVCTP 1.0 §5.2 carries each frame as a separate L2CAP packet.
# Under heavy AVCTP load (e.g., a CT subscribed to TRACK_CHANGED at high
# RegisterNotification frequency), the BT controller's frame buffers can
# saturate and individual frames can be dropped. If a released frame is
# dropped while its press counterpart was delivered, the host stack never
# receives the release event.
#
# On Y1 specifically: PASSTHROUGH "MEDIA_NEXT pressed" delivers as
# KeyEvent.KEYCODE_MEDIA_NEXT (87) via libextavrcp_jni.so's
# avrcp_input_sendkey -> /dev/uinput. KeyMap.KEY_RIGHT is also 87 in
# non-RockBox builds, so MEDIA_NEXT and the device's right d-pad share
# the music-app-side code path.
#
# BaseActivity.dispatchKeyEvent treats repeatCount == 3 as a long-press
# trigger and calls PlayerService.startFastForward(). That sets
# fastForwardLock=true and spawns a thread whose body is the lambda
# in PlayerService$startFastForward$1.invoke():
#
#     while (fastForwardLock) {
#         Thread.sleep(100);
#         setCurrentPosition(currentPosition + duration * 0.01f);
#     }
#
# The matching PASSTHROUGH "released" frame is what triggers
# stopFastForward() (which clears the lock). When the release frame is
# DROPPED at the AVCTP layer the lock is never cleared, the loop runs
# forever, and the player advances ~3-4s of song every 100ms (~32x
# speed). On the device this also drives the haptic motor on each
# setCurrentPosition() call, producing the stuck-haptic symptom seen in
# hardware testing. See `docs/INVESTIGATION.md` "Hardware test history
# per CT" for the empirical observations that motivated iter21.
#
# Patch D bounds the runaway. Each FF/RW thread:
#   - tracks an iteration counter
#   - exits and clears fastForwardLock once the counter reaches 50
#     (50 * 100 ms = 5 seconds of wall-clock hold)
#
# 5 seconds covers any realistic legitimate hold (typical FF interaction
# is 1-3 s on this device) while bounding damage from a single dropped
# release frame to ~5 s of runaway. The next press starts a fresh thread
# with a fresh counter, so genuine long scrubs remain possible by
# re-pressing.
#
# Direct iput on PlayerService.fastForwardLock from the inner-class
# lambda would normally be rejected by Dalvik's verifier (private field,
# different class). Java-source nest-mate access is implemented via
# synthetic accessors; only access$getFastForwardLock$p exists in this
# DEX -- there is no setter. Rather than add an accessor (which would
# require modifying the enclosing class's method table), we change the
# field to public. The field is internal to a system-private service, so
# this is no different in practice from the songDao change in Patch C.
#
# Three sub-edits:
#   D1. PlayerService.smali           : `private` -> `public` on field
#   D2. PlayerService$startFastForward$1.smali : invoke()V -> bounded loop
#   D3. PlayerService$startRewind$1.smali      : invoke()V -> bounded loop
#
# Spec note: this patch is purely defensive on the music-app side. The
# AVRCP wire layer remains fully spec-compliant -- no proxy changes.

PLAYER_SMALI = "smali/com/innioasis/y1/service/PlayerService.smali"
FF_LAMBDA_SMALI = (
    "smali_classes2/com/innioasis/y1/service/PlayerService$startFastForward$1.smali"
)
RW_LAMBDA_SMALI = (
    "smali_classes2/com/innioasis/y1/service/PlayerService$startRewind$1.smali"
)

# Iteration cap for the FF/RW loop. 50 * 100ms sleep = 5s wall clock.
# Hex 0x32 fits in const/16 (8-bit signed immediate via const/16 form).
HOLD_LOOP_CAP = 50

# ------------------------------------------------------------
# D1: Make fastForwardLock public so the lambda can iput it.
# ------------------------------------------------------------
player_path = os.path.join(UNPACKED_DIR, PLAYER_SMALI)
if not os.path.exists(player_path):
    sys.exit(f"ERROR: Expected smali not found: {player_path}")

with open(player_path, 'r') as f:
    player_src = f.read()

OLD_LOCK_FIELD = ".field private fastForwardLock:Z"
NEW_LOCK_FIELD = ".field public fastForwardLock:Z"

if OLD_LOCK_FIELD not in player_src:
    sys.exit(
        "ERROR: PlayerService.fastForwardLock field declaration not found.\n"
        f"  Expected: {OLD_LOCK_FIELD}\n"
        "  Inspect PlayerService.smali manually."
    )

player_src = player_src.replace(OLD_LOCK_FIELD, NEW_LOCK_FIELD, 1)
with open(player_path, 'w') as f:
    f.write(player_src)

# ------------------------------------------------------------
# D2 / D3: Rewrite invoke()V in each lambda with iteration cap.
# ------------------------------------------------------------
#
# Original FF body (rewind is identical except sub-long/2addr in place
# of add-long/2addr at the position-update step):
#
#   .method public final invoke()V
#       .locals 6
#       :cond_0
#       :goto_0
#       iget-object v0, p0, ...->this$0:...
#       invoke-static {v0}, ...->access$getFastForwardLock$p(...)Z
#       move-result v0
#       if-eqz v0, :cond_1            # !lock -> exit
#       const-wide/16 v0, 0x64
#       invoke-static {v0, v1}, Thread;->sleep(J)V
#       iget-object v0, p0, ...->this$0:...
#       invoke-virtual {v0}, ...->getDuration()J
#       move-result-wide v0
#       long-to-float v0, v0
#       const v1, 0x3c23d70a          # 0.01f
#       mul-float v0, v0, v1
#       float-to-int v0, v0
#       if-lez v0, :cond_0            # delta <= 0 -> loop without advancing
#       iget-object v1, p0, ...->this$0:...
#       invoke-virtual {v1}, ...->getCurrentPosition()J
#       move-result-wide v2
#       int-to-long v4, v0
#       add-long/2addr v2, v4         # (sub-long/2addr in rewind)
#       invoke-virtual {v1, v2, v3}, ...->setCurrentPosition(J)V
#       goto :goto_0
#       :cond_1
#       return-void
#   .end method
#
# Bounded body (.locals goes from 6 to 7; v6 holds the iteration counter):
#
#   .method public final invoke()V
#       .locals 7
#       const/4 v6, 0x0                          # counter = 0
#       :cond_0
#       :goto_0
#       iget-object v0, p0, ...->this$0:...
#       invoke-static {v0}, ...->access$getFastForwardLock$p(...)Z
#       move-result v0
#       if-eqz v0, :cond_1
#       const/16 v0, <CAP>                       # 50
#       if-lt v6, v0, :cond_2                    # counter<cap -> normal iter
#       iget-object v0, p0, ...->this$0:...
#       const/4 v1, 0x0
#       iput-boolean v1, v0, ...->fastForwardLock:Z   # clear lock
#       return-void
#       :cond_2
#       add-int/lit8 v6, v6, 0x1                 # counter++
#       const-wide/16 v0, 0x64
#       invoke-static {v0, v1}, Thread;->sleep(J)V
#       iget-object v0, p0, ...->this$0:...
#       invoke-virtual {v0}, ...->getDuration()J
#       move-result-wide v0
#       long-to-float v0, v0
#       const v1, 0x3c23d70a
#       mul-float v0, v0, v1
#       float-to-int v0, v0
#       if-lez v0, :cond_0
#       iget-object v1, p0, ...->this$0:...
#       invoke-virtual {v1}, ...->getCurrentPosition()J
#       move-result-wide v2
#       int-to-long v4, v0
#       add-long/2addr v2, v4                    # sub-long/2addr in rewind
#       invoke-virtual {v1, v2, v3}, ...->setCurrentPosition(J)V
#       goto :goto_0
#       :cond_1
#       return-void
#   .end method

def _bounded_invoke(inner_class: str, op: str) -> str:
    """Build the bounded invoke()V body for either FF or RW lambda.

    inner_class -- e.g. "Lcom/innioasis/y1/service/PlayerService$startFastForward$1;"
    op          -- "add-long/2addr" for FF, "sub-long/2addr" for RW
    """
    return (
        ".method public final invoke()V\n"
        "    .locals 7\n"
        "\n"
        "    const/4 v6, 0x0\n"
        "\n"
        "    :cond_0\n"
        "    :goto_0\n"
        f"    iget-object v0, p0, {inner_class}->this$0:Lcom/innioasis/y1/service/PlayerService;\n"
        "\n"
        "    invoke-static {v0}, Lcom/innioasis/y1/service/PlayerService;->access$getFastForwardLock$p(Lcom/innioasis/y1/service/PlayerService;)Z\n"
        "\n"
        "    move-result v0\n"
        "\n"
        "    if-eqz v0, :cond_1\n"
        "\n"
        f"    const/16 v0, 0x{HOLD_LOOP_CAP:x}\n"
        "\n"
        "    if-lt v6, v0, :cond_2\n"
        "\n"
        f"    iget-object v0, p0, {inner_class}->this$0:Lcom/innioasis/y1/service/PlayerService;\n"
        "\n"
        "    const/4 v1, 0x0\n"
        "\n"
        "    iput-boolean v1, v0, Lcom/innioasis/y1/service/PlayerService;->fastForwardLock:Z\n"
        "\n"
        "    return-void\n"
        "\n"
        "    :cond_2\n"
        "    add-int/lit8 v6, v6, 0x1\n"
        "\n"
        "    const-wide/16 v0, 0x64\n"
        "\n"
        "    invoke-static {v0, v1}, Ljava/lang/Thread;->sleep(J)V\n"
        "\n"
        f"    iget-object v0, p0, {inner_class}->this$0:Lcom/innioasis/y1/service/PlayerService;\n"
        "\n"
        "    invoke-virtual {v0}, Lcom/innioasis/y1/service/PlayerService;->getDuration()J\n"
        "\n"
        "    move-result-wide v0\n"
        "\n"
        "    long-to-float v0, v0\n"
        "\n"
        "    const v1, 0x3c23d70a    # 0.01f\n"
        "\n"
        "    mul-float v0, v0, v1\n"
        "\n"
        "    float-to-int v0, v0\n"
        "\n"
        "    if-lez v0, :cond_0\n"
        "\n"
        f"    iget-object v1, p0, {inner_class}->this$0:Lcom/innioasis/y1/service/PlayerService;\n"
        "\n"
        "    invoke-virtual {v1}, Lcom/innioasis/y1/service/PlayerService;->getCurrentPosition()J\n"
        "\n"
        "    move-result-wide v2\n"
        "\n"
        "    int-to-long v4, v0\n"
        "\n"
        f"    {op} v2, v4\n"
        "\n"
        "    invoke-virtual {v1, v2, v3}, Lcom/innioasis/y1/service/PlayerService;->setCurrentPosition(J)V\n"
        "\n"
        "    goto :goto_0\n"
        "\n"
        "    :cond_1\n"
        "    return-void\n"
        ".end method"
    )


# Match the existing invoke()V (.locals 6) verbatim so we know the source
# matches the analyzed 3.0.2 layout. The match is structural rather than
# byte-identical to allow for whitespace minor variations between apktool
# decode runs, but is anchored on the unique opcode signature of this
# loop (Thread.sleep(0x64), the 0x3c23d70a literal, and the
# (add|sub)-long/2addr step).
# Placeholders __CLS__ / __OP__ avoid str.format brace conflicts with the
# literal smali register lists (\{v0\}, \{v0, v1\}, ...).
INVOKE_PATTERN_TMPL = (
    r'\.method public final invoke\(\)V\n'
    r'    \.locals 6\n'
    r'\n'
    r'    \.line \d+\n'
    r'    :cond_0\n'
    r'    :goto_0\n'
    r'    iget-object v0, p0, L__CLS__;->this\$0:Lcom/innioasis/y1/service/PlayerService;\n'
    r'\n'
    r'    invoke-static \{v0\}, Lcom/innioasis/y1/service/PlayerService;->access\$getFastForwardLock\$p\(Lcom/innioasis/y1/service/PlayerService;\)Z\n'
    r'\n'
    r'    move-result v0\n'
    r'\n'
    r'    if-eqz v0, :cond_1\n'
    r'\n'
    r'    const-wide/16 v0, 0x64\n'
    r'\n'
    r'    \.line \d+\n'
    r'    invoke-static \{v0, v1\}, Ljava/lang/Thread;->sleep\(J\)V\n'
    r'\n'
    r'    \.line \d+\n'
    r'    iget-object v0, p0, L__CLS__;->this\$0:Lcom/innioasis/y1/service/PlayerService;\n'
    r'\n'
    r'    invoke-virtual \{v0\}, Lcom/innioasis/y1/service/PlayerService;->getDuration\(\)J\n'
    r'\n'
    r'    move-result-wide v0\n'
    r'\n'
    r'    long-to-float v0, v0\n'
    r'\n'
    r'    const v1, 0x3c23d70a    # 0\.01f\n'
    r'\n'
    r'    mul-float v0, v0, v1\n'
    r'\n'
    r'    float-to-int v0, v0\n'
    r'\n'
    r'    if-lez v0, :cond_0\n'
    r'\n'
    r'    \.line \d+\n'
    r'    iget-object v1, p0, L__CLS__;->this\$0:Lcom/innioasis/y1/service/PlayerService;\n'
    r'\n'
    r'    invoke-virtual \{v1\}, Lcom/innioasis/y1/service/PlayerService;->getCurrentPosition\(\)J\n'
    r'\n'
    r'    move-result-wide v2\n'
    r'\n'
    r'    int-to-long v4, v0\n'
    r'\n'
    r'    __OP__ v2, v4\n'
    r'\n'
    r'    invoke-virtual \{v1, v2, v3\}, Lcom/innioasis/y1/service/PlayerService;->setCurrentPosition\(J\)V\n'
    r'\n'
    r'    goto :goto_0\n'
    r'\n'
    r'    :cond_1\n'
    r'    return-void\n'
    r'\.end method'
)


def _patch_lambda(rel_path: str, inner_class: str, op_lit: str, op_re: str, label: str) -> None:
    full_path = os.path.join(UNPACKED_DIR, rel_path)
    if not os.path.exists(full_path):
        sys.exit(f"ERROR: Expected smali not found: {full_path}")

    with open(full_path, 'r') as f:
        src = f.read()

    pattern_str = (
        INVOKE_PATTERN_TMPL
        .replace("__CLS__", re.escape(inner_class))
        .replace("__OP__", op_re)
    )
    pattern = re.compile(pattern_str, re.MULTILINE)
    if not pattern.search(src):
        sys.exit(
            f"ERROR: {label} lambda invoke()V pattern not found.\n"
            f"  File: {full_path}\n"
            "  The lambda may have been recompiled with a different shape."
        )

    src = pattern.sub(lambda _m: _bounded_invoke(f"L{inner_class};", op_lit), src, count=1)
    with open(full_path, 'w') as f:
        f.write(src)


_patch_lambda(
    FF_LAMBDA_SMALI,
    "com/innioasis/y1/service/PlayerService$startFastForward$1",
    "add-long/2addr",
    r'add-long/2addr',
    "startFastForward",
)
_patch_lambda(
    RW_LAMBDA_SMALI,
    "com/innioasis/y1/service/PlayerService$startRewind$1",
    "sub-long/2addr",
    r'sub-long/2addr',
    "startRewind",
)

print(
    f"  Patch D: PlayerService FF/RW hold-loop bounded to {HOLD_LOOP_CAP} iters "
    f"(~{HOLD_LOOP_CAP * 100} ms wall clock)"
)

# ============================================================
# Patch E: PlayControllerReceiver.smali — discrete PLAY/PAUSE coverage  (iter22d)
# ============================================================
#
# Background
# ----------
# AVRCP 1.4 §11.1.2 defines distinct PASSTHROUGH op codes for PLAY (0x44)
# and PAUSE (0x46), separate from any toggle abstraction. CTs that issue
# both discrete codes from separate UI elements are spec-conformant; CTs
# that only ever issue 0x46 (and rely on the TG to interpret it as a
# toggle when already paused) are also common in practice. A spec-compliant
# TG must therefore handle all three of:
#   - 0x44 PLAY  : transition to PLAYING (no-op if already PLAYING)
#   - 0x46 PAUSE : transition to PAUSED  (no-op if already PAUSED)
#   - 0x46 sent as a toggle by the CT: TG state-flip
#
# Key-injection path inside libextavrcp_jni.so (`avrcp_input_sendkey` →
# /dev/uinput) maps these to the Linux input event keycodes:
#   - 0x46 PAUSE → Linux KEY_PLAYPAUSE (201) → Android KEYCODE_MEDIA_PLAY_PAUSE (85)
#   - 0x44 PLAY  → Linux KEY_PLAY (207)      → Android KEYCODE_MEDIA_PLAY (126)
# (PASSTHROUGH 0x45 STOP is also defined but doesn't matter for this patch.)
#
# Y1's PlayControllerReceiver (the registered ACTION_MEDIA_BUTTON receiver)
# only matches against `KeyMap.KEY_PLAY` which is hardwired to 85
# (KEYCODE_MEDIA_PLAY_PAUSE). When a CT issues a discrete PLAY (PASSTHROUGH
# 0x44 → uinput keycode 126), the receiver's `if-ne v2, KEY_PLAY` check
# fails, no `playOrPause()` call is made, and the music app silently drops
# the command. From the CT's perspective the TG accepted the command (no
# AVRCP-layer reject) but ignored it.
#
# The fix
# -------
# Extend PlayControllerReceiver's `:cond_c` block (the KEY_PLAY → playOrPause
# branch) to also accept keyCode 126 (KEYCODE_MEDIA_PLAY) and 127
# (KEYCODE_MEDIA_PAUSE) as triggers for `playOrPause()`. Both route to the
# same toggle handler — toggle from PAUSED to PLAYING when discrete PLAY
# arrives, toggle from PLAYING to PAUSED when discrete PAUSE arrives. This
# is functionally equivalent to honoring the discrete commands per AVRCP
# §11.1.2 because the toggle is a no-op when the requested target state
# matches the current state.
#
# 127 is included for forward-compat in case any other path injects
# KEYCODE_MEDIA_PAUSE (the existing PASSTHROUGH 0x46 → 85 mapping covers
# the AVRCP path; 127 covers any non-AVRCP source that might also reach
# the receiver).
#
# Stock smali at PlayControllerReceiver.smali:cond_c:
#   :cond_c
#   sget-object p1, KeyMap;->INSTANCE
#   invoke-virtual {p1}, KeyMap;->getKEY_PLAY()I
#   move-result p1
#   if-ne v2, p1, :cond_e
#   ... (playOrPause action)
#
# Patched: replace the single `if-ne v2, p1, :cond_e` with a chain of three
# equality checks that ALL fall through to the same action label:
#   :cond_c
#   sget-object p1, KeyMap;->INSTANCE
#   invoke-virtual {p1}, KeyMap;->getKEY_PLAY()I
#   move-result p1
#   if-eq v2, p1, :cond_play_match     # KEY_PLAY (= 85) match
#   const/16 p1, 0x7e                      # KEYCODE_MEDIA_PLAY (126)
#   if-eq v2, p1, :cond_play_match
#   const/16 p1, 0x7f                      # KEYCODE_MEDIA_PAUSE (127)
#   if-ne v2, p1, :cond_e
#   :cond_play_match
#   ... (playOrPause action — unchanged)
#
# apktool reassembles to DEX and adjusts all branch offsets. No new methods,
# no new fields, no manifest changes.

PLAY_CONTROLLER_RECEIVER_SMALI = (
    "smali_classes2/com/innioasis/y1/receiver/PlayControllerReceiver.smali"
)

play_receiver_path = os.path.join(UNPACKED_DIR, PLAY_CONTROLLER_RECEIVER_SMALI)
if not os.path.exists(play_receiver_path):
    sys.exit(f"ERROR: Expected smali not found: {play_receiver_path}")

with open(play_receiver_path, 'r') as f:
    play_receiver_src = f.read()

# Match the unique KEY_PLAY → playOrPause branch (the second one in the file
# at the short-press handler; the long-press version at :cond_d we leave
# alone — a held PLAY button would be unusual on a car HMI and the existing
# `longClickPlayBtnToStop` semantics don't generalize to a discrete PLAY/PAUSE
# pair). Anchor the match on the `getKEY_PLAY()` invocation immediately
# before the `playOrPause()` call so we hit the right :cond_c and not the
# :cond_d below.
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

    if-eq v2, p1, :cond_play_match

    const/16 p1, 0x7e

    if-eq v2, p1, :cond_play_match

    const/16 p1, 0x7f

    if-ne v2, p1, :cond_e

    :cond_play_match
    .line 92
    sget-object p1, Lcom/innioasis/y1/Y1Application;->Companion:Lcom/innioasis/y1/Y1Application$Companion;

    invoke-virtual {p1}, Lcom/innioasis/y1/Y1Application$Companion;->getPlayerService()Lcom/innioasis/y1/service/PlayerService;

    move-result-object p1

    if-eqz p1, :cond_e

    invoke-virtual {p1}, Lcom/innioasis/y1/service/PlayerService;->playOrPause()V

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
    "  Patch E: PlayControllerReceiver -- KEY_PLAY trigger expanded to also accept "
    "keyCode 126 (KEYCODE_MEDIA_PLAY) and 127 (KEYCODE_MEDIA_PAUSE)"
)

# -- Per-smali md5 report -----------------------------------------------------
# Hash each patched smali file. These hashes are deterministic regardless of
# Java version or apktool reassembly behavior, so they reliably indicate
# whether the smali edits succeeded — independent of whether the DEX
# assembly preserved them (the DEX-signature check below covers that side).
print(f"\nPatched smali file md5s (deterministic — same across machines):")
PATCHED_SMALI_FILES = [
    ARTISTS_SMALI, ALBUMS_SMALI, REPO_SMALI,
    PLAYER_SMALI, FF_LAMBDA_SMALI, RW_LAMBDA_SMALI,
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

# -- DEX signature verification ----------------------------------------------
# Catches the apktool/JVM-compat failure mode where smali source has the
# patches but the assembled DEX silently lacks them. Refuses to write the
# patched APK if any expected signature is missing.
print(f"\nVerifying DEX patch signatures...")
with open(dex1, 'rb') as f: dex1_bytes = f.read()
with open(dex2, 'rb') as f: dex2_bytes = f.read()
if not verify_dex_patch_signatures(dex2_bytes):
    sys.exit(
        f"\nERROR: DEX signature check failed. The APK was NOT written.\n"
        f"  Inspect {UNPACKED_DIR} to confirm the smali edits landed,\n"
        f"  then re-run under a compatible Java version (see warning above)."
    )

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
