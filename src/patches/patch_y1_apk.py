#!/usr/bin/env python3
"""
patch_y1_apk.py — smali patches on the Y1 music player APK.

Patches (per docs/PATCHES.md ## patch_y1_apk.py):
  A/B/C  Artist→Album navigation (tapping an artist drills into that
         artist's albums instead of a flat song list).
  B5/B6  In-music-app TrackInfoWriter + PlaybackStateBridge +
         BatteryReceiver + PappSetFileObserver injection (companions to
         the libextavrcp_jni.so trampoline chain).
  E      Discrete PASSTHROUGH routing in PlayControllerReceiver
         (PLAY / PAUSE / STOP / NEXT / PREVIOUS).
  H      BaseActivity / BasePlayerActivity dispatchKeyEvent propagates
         unhandled media keys past the foreground activity, with a
         framework-synthetic-repeat filter.

Output APK keeps the stock META-INF/ signature block (stale but
parseable). Must be deployed directly to /system/app/, not via
`adb install` — see README "Deployment notes".

Requirements: Python 3.8+, Java 11–21 (apktool 2.9.3's smali assembler
silently drops on Java 22+), androguard. apktool jar is auto-downloaded
into tools/ on first run (md5-verified).

Usage:
    python3 patch_y1_apk.py <com.innioasis.y1*.apk>
    python3 patch_y1_apk.py [--skip-md5] [--clean-staging] <apk>
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
# downloaded apktool jar and the decoded / rebuilt smali tree are both retained
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
APKTOOL_MD5     = "e28e4b4a413a252617d92b657a33c947"

# Why apktool 2.9.3 and not a newer release:
#   - apktool 2.10.x / 2.11.x / 2.12.x / 3.0.x have all changed the `b`
#     workflow to write DEXes only into a final dist/<name>.apk rather than
#     leaving them in build/apk/ when aapt fails (which is what we exploited
#     with --no-res to skip resource processing). Each new release would
#     require reworking the patcher's DEX-extraction step.
#   - apktool 2.9.3's bundled smali assembler (smali 2.5.x, baksmali 2.5.x)
#     does NOT support Java 22+ JVMs reliably — historical observation:
#     against Java 25, it silently dropped DEX-assembly edits in pairs of
#     similar lambda methods while preserving one of them. Pin to Java
#     11–21 if you hit assembler weirdness.
#
# Practical recommendation: run the patcher under Java 11–21. If your flash
# box is on Java 22+, install OpenJDK 21 alongside (Debian / Ubuntu:
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

# === DEBUG LOGGING TOGGLE ============================================
# When True, this patcher instruments every metadata-relevant entry point
# with `Log.d("Y1Patch", ...)` so the full pipeline is observable in
# `adb logcat -s Y1Patch:*`. Coverage:
#
#   Stock (music app smali):
#     PlayControllerReceiver.onReceive
#     BaseActivity.dispatchKeyEvent / BasePlayerActivity.dispatchKeyEvent
#     PlayerService.play / pause / playOrPause / stop
#     PlayerService.nextSong / prevSong / restartPlay / playerPrepared / toRestart
#     Y1Application.onCreate (boot init sequence)
#
#   Patcher-generated smali (entry-point Log.d traces):
#     PappSetReceiver.onReceive (B3 — CT-driven Repeat/Shuffle Sets)
#     PappStateBroadcaster.sendNow + onSharedPreferenceChanged (B4 — local
#       Repeat/Shuffle changes → AVRCP broadcast)
#
#   Inject tree (entry-point Log.d traces):
#     TrackInfoWriter — init, setPlayStatus, onSeek, markCompletion, markError,
#       onFreshTrackChange, onTrackEdge, setBattery, setPapp, flush, flushLocked,
#       wakeTrackChanged, wakePlayStateChanged
#     PlaybackStateBridge — onPlayValue, onEarlyTrackChange, onPrepared,
#       onPlayerPreparedTail, onCompletion, onSeek, onError
#     PositionTicker — start, stop, run (1 s tick)
#     BatteryReceiver — register, onReceive
#     PappSetFileObserver — start, onEvent, dispatch
#     NowPlayingRefresher — onResume, onPause, refresh, run
#
#   Inject tree (value-bearing inline _dbgKV — surfaces actual runtime values):
#     TrackInfoWriter.onTrackEdge      → onTE.old, onTE.new, onTE.EDGE_DETECTED
#     TrackInfoWriter.flushLocked      → fL.id, fL.pos, fL.dur, fL.ps
#     TrackInfoWriter.onSeek           → onSeek.in, onSeek.SUPPRESSED.dtMs,
#                                        onSeek.APPLIED.pos
#     TrackInfoWriter.setPlayStatus    → sPS.from, sPS.to
#     PlaybackStateBridge.onPlayValue  → oPV.newVal, oPV.reason
#
#   Inject tree (trampoline-state byte dump — §6.7.1 gate + mirror visibility):
#     y1-trampoline-state full layout (verified per
#     architecture_y1_subscription_gating memory):
#       [0..7]   = audio_id mirror (BE u64) — what T5 last advertised
#       [8..11]  = position mirror (BE u32) — what T9 last advertised
#       [12]     = play_status mirror      — what T9 last advertised
#       [13..19] = §6.7.1 per-subscription gates
#                  [13]=TRACK_CHANGED [14]=PLAYBACK_STATUS [15]=POS
#                  [16]=BATT          [17]=PAPP            [18..19]=reserved
#     Tag pattern (each wake produces both):
#       TrackInfoWriter.wakeTrackChanged      → wTC.pre tramp.state[0..19]=…
#                                               wTC.post tramp.state[0..19]=…
#       TrackInfoWriter.wakePlayStateChanged  → wPSC.pre tramp.state[0..19]=…
#                                               wPSC.post tramp.state[0..19]=…
#     The .pre dump fires immediately before sendBroadcast(); the .post dump
#     is posted to the main looper +50 ms later via DbgPostReadRunnable (a
#     debug-only injected class). Comparing pre vs post answers "did T5/T9
#     actually emit CHANGED on the wire?" — the relevant gate byte clears
#     between pre and post if it did; stays armed if it got gated out.
#
# Toggled at build time via the `KOENSAYR_DEBUG` environment variable —
# `apply.bash --debug` sets it. Omit for release builds (zero runtime
# overhead — no helpers and no log calls are emitted into the smali).
#
# Companion flags: same-named constant in src/patches/_trampolines.py,
# src/patches/patch_libextavrcp_jni.py, src/patches/patch_mtkbt_odex.py
# (currently placeholders; will be wired up as needed when we extend
# the diagnostic to native trampolines or the AVRCP Java dispatcher).
# Y1Bridge already uses Log.d freely; no toggle needed there.
DEBUG_LOGGING = os.environ.get("KOENSAYR_DEBUG", "") == "1"

ARTISTS_SMALI = "smali_classes2/com/innioasis/music/ArtistsActivity.smali"
ALBUMS_SMALI  = "smali_classes2/com/innioasis/music/AlbumsActivity.smali"
REPO_SMALI    = "smali/com/innioasis/y1/database/Y1Repository.smali"
Y1APP_SMALI   = "smali/com/innioasis/y1/Y1Application.smali"
PAPP_RECEIVER_SMALI = "smali/com/koensayr/PappSetReceiver.smali"
PAPP_BROADCASTER_SMALI = "smali/com/koensayr/PappStateBroadcaster.smali"

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
    description="Innioasis Y1 com.innioasis.y1 APK smali patcher (Artist→Album + discrete PASSTHROUGH PLAY / PAUSE / STOP / NEXT / PREVIOUS).",
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
# JVM-version-sensitive on Java 22+ -- Java 25 has been observed to
# silently drop one of a pair of similar lambda-method edits during DEX
# reassembly while preserving the other. Warn so the user can pin to
# Java 11-21 if they hit unexpected behavior.
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
# Patch E: PlayControllerReceiver.smali — discrete PLAY/PAUSE/STOP coverage
# ============================================================
#
# Background
# ----------
# AVRCP 1.3 §4.6.1 (PASS THROUGH command — actual op-code table and
# press/release semantics defined in AV/C Panel Subunit Specification, ref
# [2] of AVRCP 1.3) gives distinct codes for PLAY (0x44), STOP (0x45), and
# PAUSE (0x46), separate from any toggle abstraction. AVRCP 1.3 §19.3
# (Appendix D, informative) shows a concrete PASSTHROUGH PLAY frame with
# operation_ID 0x44 and confirms `state_flag = 0` (press) / `1` (release).
# CTs that issue discrete codes from separate UI elements are spec-conformant;
# CTs that only ever issue 0x46 (and rely on the TG to interpret it as a
# toggle when already paused) are also common in practice.
#
# Per the AVRCP ICS Table 8 (operation_id of category 1 for TG, see
# `docs/spec/AVRCP 1.3/AVRCP.ICS.p17.pdf` §1.5), op_ids 0x44 PLAY (item 19) and 0x45
# STOP (item 20) are **Mandatory** for any TG advertising PASS THROUGH
# Cat 1 (which we do via the V1 SDP record patch). 0x46 PAUSE (item 21)
# and 0x4B/0x4C FORWARD/BACKWARD (items 26/27) are Optional.
#
# A spec-compliant TG must therefore handle:
#   - 0x44 PLAY  : transition to PLAYING from any state (no-op if already PLAYING)  [M]
#   - 0x45 STOP  : transition to STOPPED state (release media position)             [M]
#   - 0x46 PAUSE : transition to PAUSED from any state (no-op if already PAUSED)    [O]
#   - 0x46 sent as a toggle by the CT: TG state-flip                                [O, observed]
#
# Key-injection path inside libextavrcp_jni.so (`avrcp_input_sendkey` →
# /dev/uinput) maps these to the Linux input event keycodes (verified against
# /system/usr/keylayout/AVRCP.kl):
#   - 0x44 PLAY  → Linux KEY_PLAYCD (200)  → Android KEYCODE_MEDIA_PLAY (126)
#   - 0x45 STOP  → Linux KEY_STOPCD (166)  → Android KEYCODE_MEDIA_STOP (86)
#   - 0x46 PAUSE → Linux KEY_PAUSECD (201) → Android KEYCODE_MEDIA_PLAY_PAUSE (85)
#
# Y1's PlayControllerReceiver (the registered ACTION_MEDIA_BUTTON receiver)
# stock-only matches against `KeyMap.KEY_PLAY` (= 85, KEYCODE_MEDIA_PLAY_PAUSE).
# When a CT issues a discrete PLAY (PASSTHROUGH 0x44 → uinput KEY_PLAYCD →
# KEYCODE_MEDIA_PLAY 126), the receiver's `if-ne v2, KEY_PLAY` check fails,
# no action runs, and the music app silently drops the command.
#
# The fix
# -------
# Distinguish four cases in the receiver and route each to the *correct*
# PlayerService method:
#
#   - KEY_PLAY (85, KEYCODE_MEDIA_PLAY_PAUSE):
#       call `playOrPause()V` (toggle).
#       The legacy ACTION_MEDIA_BUTTON broadcast Intent always uses 85, and
#       toggle is the right semantics for a single physical play/pause key.
#
#   - KEYCODE_MEDIA_PLAY (0x7e, 126):
#       If `isPlaying()` → `playOrPause()V` (effectively pause). Else
#       → `play(Z)V` with bool=true (start playback).
#       Per AVRCP 1.3 §4.6 PLAY is "begin/continue playing" (no toggle),
#       but some non-spec CTs map their on-screen pause button to
#       PASSTHROUGH PLAY and rely on the TG to toggle. Treating PLAY-
#       while-already-playing as pause matches what AOSP-based players
#       do in practice. Spec-compliant CTs never send PLAY against a
#       playing TG, so this branch only fires for the non-compliant case.
#       The play(true) branch's boolean runs `Static.setPlayValue(1, ...)`
#       after player start(), propagating the resume edge to UI / RCC /
#       AudioFocus.
#
#   - KEYCODE_MEDIA_PAUSE (0x7f, 127) → `pause(IZ)V` with reason=0x12,
#     flag=true. reason is a diagnostic Timber tag (stock spans 0xc-0x11);
#     0x12 is a fresh tag for the PASSTHROUGH path. flag=true matches
#     Kotlin's pause$default behaviour.
#
#   - KEYCODE_MEDIA_STOP (0x56, 86) → `stop()V`. PlayerService.stop()
#     calls IjkMediaPlayer.stop() + reset() + MediaPlayer.stop().
#     Spec-mandated per AVRCP ICS Table 8 item 20.
#
#   - KEYCODE_MEDIA_NEXT (0x57, 87) → `nextSong()V`, AV/C op 0x4B.
#   - KEYCODE_MEDIA_PREVIOUS (0x58, 88) → `prevSong()V`, AV/C op 0x4C.
#     Reached via Patch H/H′ propagation; AVRCP 1.3 §4.6.1 separates
#     NEXT/PREV from FAST_FORWARD/REWIND (op 0x49/0x48).
#
# Per-keycode split (vs. routing everything to playOrPause toggle)
# matters for strict CTs that issue discrete PLAY against an already-
# PLAYING TG — toggle would invert the CT's intent on each press.
#
# Stock smali at PlayControllerReceiver.smali:cond_c:
#   :cond_c
#   sget-object p1, KeyMap;->INSTANCE
#   invoke-virtual {p1}, KeyMap;->getKEY_PLAY()I
#   move-result p1
#   if-ne v2, p1, :cond_e
#   ... (playOrPause action — single arm)
#
# Patched:
#   :cond_c
#   sget-object p1, KeyMap;->INSTANCE
#   invoke-virtual {p1}, KeyMap;->getKEY_PLAY()I
#   move-result p1
#   if-eq v2, p1, :cond_play_pause_toggle    # KEY_PLAY (85) → toggle
#   const/16 p1, 0x7e
#   if-eq v2, p1, :cond_play_strict          # MEDIA_PLAY (126) → play(true)
#   const/16 p1, 0x7f
#   if-eq v2, p1, :cond_pause_strict         # MEDIA_PAUSE (127) → pause()
#   const/16 p1, 0x56
#   if-eq v2, p1, :cond_stop_strict          # MEDIA_STOP (86) → stop()
#   const/16 p1, 0x57
#   if-eq v2, p1, :cond_next_strict          # MEDIA_NEXT (87) → nextSong()
#   const/16 p1, 0x58
#   if-eq v2, p1, :cond_prev_strict          # MEDIA_PREVIOUS (88) → prevSong()
#   goto :cond_e                             # nothing matched, keep walking
#
# Each :cond_*_strict arm follows the same pattern: fetch PlayerService
# via Y1Application$Companion, null-check, invoke the discrete method,
# `goto :goto_5`. v0 and v3 are scratch (the .locals 6 onReceive method
# has v0..v5; the keyCode lives in v2 throughout). apktool will renumber
# the user-defined :cond_*_strict labels to alphanumeric :cond_X on
# reassembly — that's expected and fine.

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

    const/16 p1, 0x56

    if-eq v2, p1, :cond_stop_strict

    const/16 p1, 0x57

    if-eq v2, p1, :cond_next_strict

    const/16 p1, 0x58

    if-eq v2, p1, :cond_prev_strict

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

    invoke-virtual {p1}, Lcom/innioasis/y1/service/PlayerService;->isPlaying()Z

    move-result v0

    if-eqz v0, :cond_play_strict_start

    invoke-virtual {p1}, Lcom/innioasis/y1/service/PlayerService;->playOrPause()V

    goto :goto_5

    :cond_play_strict_start
    const/4 v0, 0x1

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

    goto :goto_5

    :cond_stop_strict
    sget-object p1, Lcom/innioasis/y1/Y1Application;->Companion:Lcom/innioasis/y1/Y1Application$Companion;

    invoke-virtual {p1}, Lcom/innioasis/y1/Y1Application$Companion;->getPlayerService()Lcom/innioasis/y1/service/PlayerService;

    move-result-object p1

    if-eqz p1, :cond_e

    invoke-virtual {p1}, Lcom/innioasis/y1/service/PlayerService;->stop()V

    goto :goto_5

    :cond_next_strict
    sget-object p1, Lcom/innioasis/y1/Y1Application;->Companion:Lcom/innioasis/y1/Y1Application$Companion;

    invoke-virtual {p1}, Lcom/innioasis/y1/Y1Application$Companion;->getPlayerService()Lcom/innioasis/y1/service/PlayerService;

    move-result-object p1

    if-eqz p1, :cond_e

    invoke-virtual {p1}, Lcom/innioasis/y1/service/PlayerService;->nextSong()V

    goto :goto_5

    :cond_prev_strict
    sget-object p1, Lcom/innioasis/y1/Y1Application;->Companion:Lcom/innioasis/y1/Y1Application$Companion;

    invoke-virtual {p1}, Lcom/innioasis/y1/Y1Application$Companion;->getPlayerService()Lcom/innioasis/y1/service/PlayerService;

    move-result-object p1

    if-eqz p1, :cond_e

    invoke-virtual {p1}, Lcom/innioasis/y1/service/PlayerService;->prevSong()V

    goto :goto_5"""

if OLD_PLAY_BRANCH not in play_receiver_src:
    sys.exit(
        "ERROR: PlayControllerReceiver KEY_PLAY → playOrPause branch not found.\n"
        f"  File: {play_receiver_path}\n"
        "  The smali shape may differ from 3.0.2."
    )

play_receiver_src = play_receiver_src.replace(OLD_PLAY_BRANCH, NEW_PLAY_BRANCH, 1)


# -- Diagnostic Log.d injection (gated by KOENSAYR_DEBUG / --debug) ----------
# Surfaces "Y1Patch" tag entries on `adb logcat -s Y1Patch:*` whenever the
# instrumented method runs. Each injection sits at the very top of the method
# body (right after `.locals N`), so v0/v1 are guaranteed-uninitialized
# scratch — no save/restore needed. The original method body re-initialises
# v0/v1 before using them, so the diagnostic is invisible to the rest of the
# code apart from the constant-time Log.d call.
def _inject_log_d(smali, method_signature_re, msg):
    """Insert a Log.d("Y1Patch", msg) call at the top of the method body.

    Matches `^.method ... <method_signature_re>$\\n    .locals N$` and
    inserts the Log.d snippet between the `.locals` line and whatever
    follows. Returns the modified smali source.

    Raises ValueError if the method signature doesn't appear exactly once,
    so silent partial-applies surface as patcher errors rather than
    invisible no-instrumentation builds.

    Bumps `.locals` to 2 if the method declares fewer (snippet uses v0/v1).
    """
    pattern = re.compile(
        rf'(^\.method[^\n]*\b{method_signature_re}\n    \.locals )(\d+)(\n)',
        re.MULTILINE,
    )
    snippet = (
        '\n'
        '    # === DIAGNOSTIC LOGGING (KOENSAYR_DEBUG=1; --debug) ===\n'
        '    const-string v0, "Y1Patch"\n'
        f'    const-string v1, "{msg}"\n'
        '    invoke-static {v0, v1}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I\n'
        '    # === END DIAGNOSTIC ===\n'
        '\n'
    )
    matches = pattern.findall(smali)
    if len(matches) != 1:
        raise ValueError(
            f"_inject_log_d: expected exactly one match for {method_signature_re!r}, "
            f"found {len(matches)}"
        )
    def _repl(m):
        prefix, n, suffix = m.group(1), int(m.group(2)), m.group(3)
        bumped = max(n, 2)
        return f"{prefix}{bumped}{suffix}{snippet}"
    return pattern.sub(_repl, smali, count=1)


if DEBUG_LOGGING:
    print("\n[Patch E debug] DEBUG_LOGGING=True (KOENSAYR_DEBUG=1) — injecting "
          "Log.d entry-point traces.")
    play_receiver_src = _inject_log_d(
        play_receiver_src,
        r'onReceive\(Landroid/content/Context;Landroid/content/Intent;\)V',
        "PlayControllerReceiver.onReceive entry",
    )
    print("  + PlayControllerReceiver.onReceive entry")

with open(play_receiver_path, 'w') as f:
    f.write(play_receiver_src)
print(
    "  Patch E: PlayControllerReceiver -- KEY_PLAY (85) → playOrPause (toggle); "
    "KEYCODE_MEDIA_PLAY (126) → play(true) [discrete PLAY per AV/C Panel Subunit op 0x44]; "
    "KEYCODE_MEDIA_PAUSE (127) → pause(0x12, true) [discrete PAUSE per op 0x46]; "
    "KEYCODE_MEDIA_STOP (86) → stop() [discrete STOP per op 0x45 — ICS Table 8 item 20 mandatory]; "
    "KEYCODE_MEDIA_NEXT (87) → nextSong() [discrete NEXT per op 0x4B]; "
    "KEYCODE_MEDIA_PREVIOUS (88) → prevSong() [discrete PREV per op 0x4C]"
)


# -- Diagnostic Log.d injection into PlayerService entry-points --------------
# play(Z)V / pause(IZ)V / playOrPause()V / stop()V each sit in PlayerService
# (separate smali file from PlayControllerReceiver). Instrumenting them lets
# us see whether the broadcast routing reached PlayerService and which method
# fired.
PLAYER_SERVICE_SMALI = "smali/com/innioasis/y1/service/PlayerService.smali"
player_service_path = os.path.join(UNPACKED_DIR, PLAYER_SERVICE_SMALI)

if DEBUG_LOGGING:
    if not os.path.exists(player_service_path):
        sys.exit(f"ERROR: Expected smali not found: {player_service_path}")
    with open(player_service_path, 'r') as f:
        player_service_src = f.read()
    # Track-change + state pipeline entry points. setCurrentPosition entry
    # is intentionally not instrumented here — its B5.2a hook routes through
    # PlaybackStateBridge.onSeek which already has its own entry trace, and
    # adding a Log.d here would invalidate the B5.2a anchor below.
    for sig, msg in (
        (r'play\(Z\)V',                  "PlayerService.play(Z) entry"),
        (r'pause\(IZ\)V',                "PlayerService.pause(IZ) entry"),
        (r'playOrPause\(\)V',            "PlayerService.playOrPause() entry"),
        (r'stop\(\)V',                   "PlayerService.stop() entry"),
        (r'nextSong\(\)V',               "PlayerService.nextSong() entry"),
        (r'prevSong\(\)V',               "PlayerService.prevSong() entry"),
        (r'restartPlay\(Z\)V',           "PlayerService.restartPlay(Z) entry"),
        (r'playerPrepared\(\)V',         "PlayerService.playerPrepared() entry"),
        (r'toRestart\(\)V',              "PlayerService.toRestart() entry"),
    ):
        player_service_src = _inject_log_d(player_service_src, sig, msg)
        print(f"  + PlayerService.{sig.replace(chr(92), '')}")
    with open(player_service_path, 'w') as f:
        f.write(player_service_src)


# ============================================================
# Patch H: BaseActivity.smali — propagate unhandled discrete media keys
# ============================================================
# Stock dispatchKeyEvent always returns TRUE, swallowing AVRCP-derived
# KEYCODE_MEDIA_PLAY/_PAUSE/_STOP/_NEXT/_PREVIOUS that don't match the
# device's KeyMap. We early-return FALSE on those keycodes for repeatCount==0
# (so they propagate to AudioService → PlayControllerReceiver Patch E discrete
# arms) and TRUE on repeatCount>0 (silent consume — defangs framework
# InputDispatcher::synthesizeKeyRepeatLocked synthesised repeats that drove
# the "stuck fast-forwarding" symptom). Full rationale + side-effects
# (hardware NEXT/PREV touch buttons lose long-press FF/RW) in
# docs/PATCHES.md Patch H section.

BASE_ACTIVITY_SMALI = "smali/com/innioasis/y1/base/BaseActivity.smali"
base_activity_path = os.path.join(UNPACKED_DIR, BASE_ACTIVITY_SMALI)
if not os.path.exists(base_activity_path):
    sys.exit(f"ERROR: Expected smali not found: {base_activity_path}")

with open(base_activity_path, 'r') as f:
    base_activity_src = f.read()

OLD_DISPATCH_HEAD = """\
.method public dispatchKeyEvent(Landroid/view/KeyEvent;)Z
    .locals 7

    const/4 v0, 0x1

    if-nez p1, :cond_0

    return v0

    .line 673
    :cond_0
    invoke-virtual {p1}, Landroid/view/KeyEvent;->getAction()I

    move-result v1

    .line 674
    invoke-virtual {p1}, Landroid/view/KeyEvent;->getKeyCode()I

    move-result v2

    const/4 v3, 0x3"""

NEW_DISPATCH_HEAD = """\
.method public dispatchKeyEvent(Landroid/view/KeyEvent;)Z
    .locals 7

    const/4 v0, 0x1

    if-nez p1, :cond_0

    return v0

    .line 673
    :cond_0
    invoke-virtual {p1}, Landroid/view/KeyEvent;->getAction()I

    move-result v1

    .line 674
    invoke-virtual {p1}, Landroid/view/KeyEvent;->getKeyCode()I

    move-result v2

    const/16 v3, 0x7e

    if-eq v2, v3, :patch_h_avrcp_key

    const/16 v3, 0x7f

    if-eq v2, v3, :patch_h_avrcp_key

    const/16 v3, 0x56

    if-eq v2, v3, :patch_h_avrcp_key

    const/16 v3, 0x57

    if-eq v2, v3, :patch_h_avrcp_key

    const/16 v3, 0x58

    if-eq v2, v3, :patch_h_avrcp_key

    goto :patch_h_continue

    :patch_h_avrcp_key
    invoke-virtual {p1}, Landroid/view/KeyEvent;->getRepeatCount()I

    move-result v3

    if-eqz v3, :patch_h_propagate

    return v0

    :patch_h_propagate
    const/4 v0, 0x0

    return v0

    :patch_h_continue
    const/4 v3, 0x3"""

if OLD_DISPATCH_HEAD not in base_activity_src:
    sys.exit(
        "ERROR: BaseActivity dispatchKeyEvent prologue not found.\n"
        f"  File: {base_activity_path}\n"
        "  The smali shape may differ from 3.0.2."
    )

base_activity_src = base_activity_src.replace(OLD_DISPATCH_HEAD, NEW_DISPATCH_HEAD, 1)

if DEBUG_LOGGING:
    base_activity_src = _inject_log_d(
        base_activity_src,
        r'dispatchKeyEvent\(Landroid/view/KeyEvent;\)Z',
        "BaseActivity.dispatchKeyEvent entry",
    )
    print("  + BaseActivity.dispatchKeyEvent entry")

with open(base_activity_path, 'w') as f:
    f.write(base_activity_src)
print(
    "  Patch H: BaseActivity.dispatchKeyEvent -- propagate KEYCODE_MEDIA_PLAY (126), "
    "MEDIA_PAUSE (127), MEDIA_STOP (86), MEDIA_NEXT (87), MEDIA_PREVIOUS (88) on "
    "first press; consume framework synthetic repeats (repeatCount > 0) silently"
)


# ============================================================
# Patch H': BasePlayerActivity.smali — same propagation, music-player class
# ============================================================
# BasePlayerActivity overrides dispatchKeyEvent and never delegates up, so
# Patch H is unreachable when the music-player screen is foreground. We
# apply the same early-return block (five keycodes + repeatCount filter)
# at the top of BasePlayerActivity.dispatchKeyEvent, before the
# Intrinsics.checkNotNull call. Detail in docs/PATCHES.md Patch H′ section.

BASE_PLAYER_ACTIVITY_SMALI = (
    "smali_classes2/com/innioasis/y1/base/BasePlayerActivity.smali"
)
base_player_activity_path = os.path.join(UNPACKED_DIR, BASE_PLAYER_ACTIVITY_SMALI)
if not os.path.exists(base_player_activity_path):
    sys.exit(f"ERROR: Expected smali not found: {base_player_activity_path}")

with open(base_player_activity_path, 'r') as f:
    base_player_activity_src = f.read()

OLD_PLAYER_DISPATCH_HEAD = """\
.method public dispatchKeyEvent(Landroid/view/KeyEvent;)Z
    .locals 2

    .line 304
    invoke-static {p1}, Lkotlin/jvm/internal/Intrinsics;->checkNotNull(Ljava/lang/Object;)V

    invoke-virtual {p1}, Landroid/view/KeyEvent;->getAction()I

    move-result v0"""

NEW_PLAYER_DISPATCH_HEAD = """\
.method public dispatchKeyEvent(Landroid/view/KeyEvent;)Z
    .locals 2

    invoke-virtual {p1}, Landroid/view/KeyEvent;->getKeyCode()I

    move-result v0

    const/16 v1, 0x7e

    if-eq v0, v1, :patch_h2_avrcp_key

    const/16 v1, 0x7f

    if-eq v0, v1, :patch_h2_avrcp_key

    const/16 v1, 0x56

    if-eq v0, v1, :patch_h2_avrcp_key

    const/16 v1, 0x57

    if-eq v0, v1, :patch_h2_avrcp_key

    const/16 v1, 0x58

    if-eq v0, v1, :patch_h2_avrcp_key

    goto :patch_h2_continue

    :patch_h2_avrcp_key
    invoke-virtual {p1}, Landroid/view/KeyEvent;->getRepeatCount()I

    move-result v0

    if-eqz v0, :patch_h2_propagate

    const/4 v0, 0x1

    return v0

    :patch_h2_propagate
    const/4 v0, 0x0

    return v0

    :patch_h2_continue

    .line 304
    invoke-static {p1}, Lkotlin/jvm/internal/Intrinsics;->checkNotNull(Ljava/lang/Object;)V

    invoke-virtual {p1}, Landroid/view/KeyEvent;->getAction()I

    move-result v0"""

if OLD_PLAYER_DISPATCH_HEAD not in base_player_activity_src:
    sys.exit(
        "ERROR: BasePlayerActivity dispatchKeyEvent prologue not found.\n"
        f"  File: {base_player_activity_path}\n"
        "  The smali shape may differ from 3.0.2."
    )

base_player_activity_src = base_player_activity_src.replace(
    OLD_PLAYER_DISPATCH_HEAD, NEW_PLAYER_DISPATCH_HEAD, 1
)

if DEBUG_LOGGING:
    base_player_activity_src = _inject_log_d(
        base_player_activity_src,
        r'dispatchKeyEvent\(Landroid/view/KeyEvent;\)Z',
        "BasePlayerActivity.dispatchKeyEvent entry",
    )
    print("  + BasePlayerActivity.dispatchKeyEvent entry")

with open(base_player_activity_path, 'w') as f:
    f.write(base_player_activity_src)
print(
    "  Patch H': BasePlayerActivity.dispatchKeyEvent -- same five-keycode "
    "propagation + repeatCount filter as Patch H, applied to the music "
    "player superclass which overrides dispatchKeyEvent and bypasses "
    "BaseActivity entirely"
)


# ============================================================
# Patch B3: PappSetReceiver — apply CT-driven Repeat/Shuffle Sets from AVRCP
# ============================================================
#
# Adds a new BroadcastReceiver `com.koensayr.PappSetReceiver` to the music
# app. The receiver listens for two intents:
#
#   ACTION_SET_REPEAT_MODE  (com.koensayr.y1.bridge.SET_REPEAT_MODE, EXTRA "value":I)
#   ACTION_SET_IS_SHUFFLE   (com.koensayr.y1.bridge.SET_IS_SHUFFLE,  EXTRA "value":Z)
#
# On receipt, it calls `SharedPreferencesUtils.INSTANCE.setMusicRepeatMode(I)`
# or `setMusicIsShuffle(Z)` — the same setters the in-app Settings screen
# calls when the Y1 user toggles Repeat / Shuffle. PlayerService re-reads
# the SharedPreferences at track-end, so the change propagates without a
# music-app restart.
#
# The live CT-Set path runs through B5's PappSetFileObserver: T_papp 0x14 in
# libextavrcp_jni.so writes y1-papp-set in the music-app dir directly, and
# PappSetFileObserver applies it via SharedPreferencesUtils — no Intent hop.
# This receiver stays in tree as a no-op safety net.
#
# Two parts:
#   1. Write a brand-new smali file (PappSetReceiver.smali) into the
#      apk's smali tree. apktool re-compiles all smali files when it
#      reassembles the DEX, so the new class is included automatically.
#   2. Inject a `registerReceiver(...)` call at the end of
#      Y1Application.onCreate() so the receiver is live as soon as the
#      app process is up. We don't need a manifest entry because dynamic
#      registration covers it.
#
# Receiver class lives under `com.koensayr` so the new class name doesn't
# collide with anything in the existing `com.innioasis.y1.*` namespace.

print(f"\nPatch B3: PappSetReceiver in music app")

PAPP_RECEIVER_SMALI_BODY = """\
.class public Lcom/koensayr/PappSetReceiver;
.super Landroid/content/BroadcastReceiver;
.source "PappSetReceiver.smali"


# direct methods
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/content/BroadcastReceiver;-><init>()V

    return-void
.end method


# virtual methods
.method public onReceive(Landroid/content/Context;Landroid/content/Intent;)V
    .locals 4

    if-eqz p2, :end

    invoke-virtual {p2}, Landroid/content/Intent;->getAction()Ljava/lang/String;

    move-result-object v0

    if-eqz v0, :end

    const-string v1, "com.koensayr.y1.bridge.SET_REPEAT_MODE"

    invoke-virtual {v0, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v1

    if-eqz v1, :try_shuffle

    # Repeat path: SharedPreferencesUtils.setMusicRepeatMode(intent.getIntExtra("value", 0))
    const-string v1, "value"

    const/4 v2, 0x0

    invoke-virtual {p2, v1, v2}, Landroid/content/Intent;->getIntExtra(Ljava/lang/String;I)I

    move-result v1

    sget-object v2, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->INSTANCE:Lcom/innioasis/y1/utils/SharedPreferencesUtils;

    invoke-virtual {v2, v1}, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->setMusicRepeatMode(I)V

    goto :end

    :try_shuffle
    const-string v1, "com.koensayr.y1.bridge.SET_IS_SHUFFLE"

    invoke-virtual {v0, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v1

    if-eqz v1, :end

    # Shuffle path: SharedPreferencesUtils.setMusicIsShuffle(intent.getBooleanExtra("value", false))
    const-string v1, "value"

    const/4 v2, 0x0

    invoke-virtual {p2, v1, v2}, Landroid/content/Intent;->getBooleanExtra(Ljava/lang/String;Z)Z

    move-result v1

    sget-object v2, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->INSTANCE:Lcom/innioasis/y1/utils/SharedPreferencesUtils;

    invoke-virtual {v2, v1}, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->setMusicIsShuffle(Z)V

    :end
    return-void
.end method
"""

papp_receiver_src = PAPP_RECEIVER_SMALI_BODY
if DEBUG_LOGGING:
    papp_receiver_src = _inject_log_d(
        papp_receiver_src,
        r'onReceive\(Landroid/content/Context;Landroid/content/Intent;\)V',
        "PappSetReceiver.onReceive entry",
    )
papp_receiver_path = os.path.join(UNPACKED_DIR, PAPP_RECEIVER_SMALI)
os.makedirs(os.path.dirname(papp_receiver_path), exist_ok=True)
with open(papp_receiver_path, 'w') as f:
    f.write(papp_receiver_src)
print(f"  Wrote {PAPP_RECEIVER_SMALI}{' (+1 entry trace; --debug)' if DEBUG_LOGGING else ''}")

# -- Inject registerReceiver into Y1Application.onCreate ----------------------
y1app_path = os.path.join(UNPACKED_DIR, Y1APP_SMALI)
if not os.path.exists(y1app_path):
    sys.exit(f"ERROR: Expected smali not found: {y1app_path}")

with open(y1app_path, 'r') as f:
    y1app_src = f.read()

# We patch the *single* return-void inside `public onCreate()V`. Match the
# preceding `:cond_3` label so we don't accidentally clobber some other
# return-void in the file.
OLD_Y1APP_RETURN = """\
    :cond_3
    return-void
.end method"""

NEW_Y1APP_RETURN = """\
    :cond_3
    # Patch B3: register PappSetReceiver for ACTION_SET_REPEAT_MODE +
    # ACTION_SET_IS_SHUFFLE so AVRCP-driven Sets land in the music app.
    new-instance v0, Lcom/koensayr/PappSetReceiver;

    invoke-direct {v0}, Lcom/koensayr/PappSetReceiver;-><init>()V

    new-instance v1, Landroid/content/IntentFilter;

    invoke-direct {v1}, Landroid/content/IntentFilter;-><init>()V

    const-string v2, "com.koensayr.y1.bridge.SET_REPEAT_MODE"

    invoke-virtual {v1, v2}, Landroid/content/IntentFilter;->addAction(Ljava/lang/String;)V

    const-string v2, "com.koensayr.y1.bridge.SET_IS_SHUFFLE"

    invoke-virtual {v1, v2}, Landroid/content/IntentFilter;->addAction(Ljava/lang/String;)V

    invoke-virtual {p0, v0, v1}, Lcom/innioasis/y1/Y1Application;->registerReceiver(Landroid/content/BroadcastReceiver;Landroid/content/IntentFilter;)Landroid/content/Intent;

    # Patch B4: register PappStateBroadcaster as OnSharedPreferenceChangeListener
    # against the "settings" SharedPreferences. On Y1-side toggle of
    # musicRepeatMode / musicIsShuffle (whether from the in-app Settings UI
    # or from the AVRCP-driven PappSetReceiver above), the broadcaster calls
    # TrackInfoWriter.setPapp() to update y1-track-info[795..796] and fires
    # com.android.music.playstatechanged so T9 wakes and emits AVRCP event
    # 0x08 CHANGED.
    new-instance v0, Lcom/koensayr/PappStateBroadcaster;

    invoke-direct {v0, p0}, Lcom/koensayr/PappStateBroadcaster;-><init>(Landroid/content/Context;)V

    const-string v1, "settings"

    const/4 v2, 0x0

    invoke-virtual {p0, v1, v2}, Lcom/innioasis/y1/Y1Application;->getSharedPreferences(Ljava/lang/String;I)Landroid/content/SharedPreferences;

    move-result-object v1

    invoke-interface {v1, v0}, Landroid/content/SharedPreferences;->registerOnSharedPreferenceChangeListener(Landroid/content/SharedPreferences$OnSharedPreferenceChangeListener;)V

    invoke-virtual {v0}, Lcom/koensayr/PappStateBroadcaster;->sendNow()V

    return-void
.end method"""

if OLD_Y1APP_RETURN not in y1app_src:
    sys.exit(
        "ERROR: Y1Application.onCreate :cond_3 + return-void not found.\n"
        f"  File: {y1app_path}\n"
        "  The smali shape may differ from 3.0.2."
    )

y1app_src = y1app_src.replace(OLD_Y1APP_RETURN, NEW_Y1APP_RETURN, 1)
with open(y1app_path, 'w') as f:
    f.write(y1app_src)
print("  Patch B3: Y1Application.onCreate registers PappSetReceiver")


# ============================================================
# Patch B4: PappStateBroadcaster — push Y1-side Repeat/Shuffle edges to
# y1-track-info + fire playstatechanged so T9 emits AVRCP event 0x08 CHANGED
# ============================================================
#
# OnSharedPreferenceChangeListener fires for any write to the "settings"
# SharedPreferences — covers both AVRCP-driven Sets (PappSetFileObserver from
# Patch B5) and Y1-UI toggles uniformly. On a key match for "musicRepeatMode"
# or "musicIsShuffle", reads the live values via SharedPreferencesUtils, maps
# to AVRCP §5.2.4 enum bytes, calls TrackInfoWriter.setPapp() to update
# y1-track-info[795..796], and fires com.android.music.playstatechanged so
# MtkBt's BluetoothAvrcpReceiver wakes notificationPlayStatusChangedNative
# → T9 → AVRCP §5.4.2 Tbl 5.36 PLAYER_APPLICATION_SETTING_CHANGED CHANGED.
#
# The ACTION_PAPP_STATE_DID_CHANGE broadcast is retained as a no-op safety
# net — Y1Bridge.apk does not consume it; the file write to y1-track-info
# is the live path.
#
# AVRCP §5.2.4 mapping (see INVESTIGATION.md Trace #18):
#   Repeat:  Y1 musicRepeatMode 0/1/2 → AVRCP 0x01/0x02/0x03 (OFF/SINGLE/ALL)
#   Shuffle: Y1 musicIsShuffle false/true → AVRCP 0x01/0x02 (OFF/ALL_TRACK)
#
# Self-rooted via a static field so the GC doesn't reclaim the listener
# (SharedPreferences holds OnSharedPreferenceChangeListener via weak ref).
# Y1Application.onCreate also calls sendNow() once on registration so the
# music-app y1-track-info[795..796] bytes are seeded at startup.

print(f"\nPatch B4: PappStateBroadcaster in music app")

PAPP_BROADCASTER_SMALI_BODY = """\
.class public Lcom/koensayr/PappStateBroadcaster;
.super Ljava/lang/Object;
.implements Landroid/content/SharedPreferences$OnSharedPreferenceChangeListener;
.source "PappStateBroadcaster.smali"


# static fields — strong self-reference so the listener survives GC.
.field private static sInstance:Lcom/koensayr/PappStateBroadcaster;


# instance fields
.field private final mContext:Landroid/content/Context;


# direct methods
.method public constructor <init>(Landroid/content/Context;)V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    iput-object p1, p0, Lcom/koensayr/PappStateBroadcaster;->mContext:Landroid/content/Context;

    sput-object p0, Lcom/koensayr/PappStateBroadcaster;->sInstance:Lcom/koensayr/PappStateBroadcaster;

    return-void
.end method

# Y1 musicRepeatMode int (0/1/2) → AVRCP §5.2.4 Tbl 5.20 byte (0x01/0x02/0x03).
.method private static repeatToAvrcp(I)I
    .locals 1

    if-nez p0, :cond_one

    const/4 v0, 0x1

    return v0

    :cond_one
    const/4 v0, 0x1

    if-ne p0, v0, :cond_all

    const/4 v0, 0x2

    return v0

    :cond_all
    const/4 v0, 0x3

    return v0
.end method

# Y1 musicIsShuffle boolean → AVRCP §5.2.4 Tbl 5.21 byte
# (true = ALL_TRACK 0x02, false = OFF 0x01).
.method private static shuffleToAvrcp(Z)I
    .locals 1

    if-eqz p0, :cond_off

    const/4 v0, 0x2

    return v0

    :cond_off
    const/4 v0, 0x1

    return v0
.end method

# Read live Repeat / Shuffle, map to AVRCP enum, broadcast.
.method public sendNow()V
    .locals 5

    sget-object v0, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->INSTANCE:Lcom/innioasis/y1/utils/SharedPreferencesUtils;

    invoke-virtual {v0}, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->getMusicRepeatMode()I

    move-result v0

    invoke-static {v0}, Lcom/koensayr/PappStateBroadcaster;->repeatToAvrcp(I)I

    move-result v0

    sget-object v1, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->INSTANCE:Lcom/innioasis/y1/utils/SharedPreferencesUtils;

    invoke-virtual {v1}, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->getMusicIsShuffle()Z

    move-result v1

    invoke-static {v1}, Lcom/koensayr/PappStateBroadcaster;->shuffleToAvrcp(Z)I

    move-result v1

    new-instance v2, Landroid/content/Intent;

    const-string v3, "com.koensayr.y1.bridge.PAPP_STATE_DID_CHANGE"

    invoke-direct {v2, v3}, Landroid/content/Intent;-><init>(Ljava/lang/String;)V

    const-string v3, "com.koensayr.y1.bridge"

    invoke-virtual {v2, v3}, Landroid/content/Intent;->setPackage(Ljava/lang/String;)Landroid/content/Intent;

    const-string v3, "repeat_avrcp"

    invoke-virtual {v2, v3, v0}, Landroid/content/Intent;->putExtra(Ljava/lang/String;I)Landroid/content/Intent;

    const-string v3, "shuffle_avrcp"

    invoke-virtual {v2, v3, v1}, Landroid/content/Intent;->putExtra(Ljava/lang/String;I)Landroid/content/Intent;

    iget-object v3, p0, Lcom/koensayr/PappStateBroadcaster;->mContext:Landroid/content/Context;

    invoke-virtual {v3, v2}, Landroid/content/Context;->sendBroadcast(Landroid/content/Intent;)V

    return-void
.end method


# virtual methods — OnSharedPreferenceChangeListener
.method public onSharedPreferenceChanged(Landroid/content/SharedPreferences;Ljava/lang/String;)V
    .locals 2

    if-eqz p2, :end

    const-string v0, "musicRepeatMode"

    invoke-virtual {v0, p2}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v1

    if-nez v1, :send

    const-string v0, "musicIsShuffle"

    invoke-virtual {v0, p2}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v1

    if-eqz v1, :end

    :send
    invoke-virtual {p0}, Lcom/koensayr/PappStateBroadcaster;->sendNow()V

    :end
    return-void
.end method
"""

papp_broadcaster_src = PAPP_BROADCASTER_SMALI_BODY
if DEBUG_LOGGING:
    for sig, msg in (
        (r'sendNow\(\)V',
            "PappStateBroadcaster.sendNow entry"),
        (r'onSharedPreferenceChanged\(Landroid/content/SharedPreferences;Ljava/lang/String;\)V',
            "PappStateBroadcaster.onSharedPreferenceChanged entry"),
    ):
        papp_broadcaster_src = _inject_log_d(papp_broadcaster_src, sig, msg)
papp_broadcaster_path = os.path.join(UNPACKED_DIR, PAPP_BROADCASTER_SMALI)
os.makedirs(os.path.dirname(papp_broadcaster_path), exist_ok=True)
with open(papp_broadcaster_path, 'w') as f:
    f.write(papp_broadcaster_src)
print(f"  Wrote {PAPP_BROADCASTER_SMALI}{' (+2 entry traces; --debug)' if DEBUG_LOGGING else ''}")


# ============================================================
# Patch B5: in-app y1-track-info production (music app = canonical writer)
# ============================================================
#
# The music app is the canonical writer of /data/data/com.innioasis.y1/files/
# y1-track-info (1104-byte schema). The libextavrcp_jni.so trampoline chain
# reads from this path directly.
#
# Components (all under com.koensayr.y1.*, copied from src/patches/inject/):
#   trackinfo/TrackInfoWriter — singleton holder + atomic file writer
#   playback/PlaybackStateBridge — static dispatcher: setPlayValue + listener lambdas
#   battery/BatteryReceiver — ACTION_BATTERY_CHANGED → AVRCP §5.4.2 Tbl 5.35 bucket
#   papp/PappSetFileObserver — FileObserver on y1-papp-set
#
# Existing-file edits (smali prepends, no logic replacement):
#   smali_classes2/com/innioasis/y1/utils/Static.smali
#     setPlayValue(II)V — prepend invoke-static PlaybackStateBridge.onPlayValue
#       (canonical state-edge entry per docs/RECON-MUSIC-APP-HOOKS.md §2)
#   smali/com/innioasis/y1/service/PlayerService.smali
#     six listener lambdas (initPlayer$lambda-{10,11,12} for IJK,
#       initPlayer2$lambda-{13,14,15} for android.media.MediaPlayer)
#   smali/com/innioasis/y1/Y1Application.smali
#     onCreate :cond_3 block — extends the existing B3+B4 registration with
#       TrackInfoWriter.init / BatteryReceiver.register / PappSetFileObserver.start
#   smali/com/koensayr/PappStateBroadcaster.smali (B4 product)
#     sendNow() — also calls TrackInfoWriter.setPapp so the music-app file
#       reflects Repeat/Shuffle changes immediately.

print(f"\nPatch B5: in-app y1-track-info production (music app is canonical writer)")

INJECT_ROOT = os.path.join(SCRIPT_DIR, "inject")

# (source-relative-to-inject, dest-relative-to-unpacked) tuples. Source files
# live under src/patches/inject/com/koensayr/y1/* (real .smali, syntax-checked
# by apktool's smali assembler at reassembly time). Drop into smali/ (primary
# DEX) so they load with Y1Application and don't depend on MultiDex.install,
# which on Dalvik 1.6 (Android 4.2.2) caches secondary-dexes under
# /data/data/com.innioasis.y1/code_cache/ and survives /system/app/ reflashes
# — a stale cache loads the pre-patch classes2.dex and TrackInfoWriter is
# nowhere to be found at runtime (NoClassDefFoundError on Y1Application.onCreate).
#
# Patch B6 (AvrcpBridgeService + AvrcpBinder) ships to smali_classes2/ because
# classes.dex is at 99.7% of the 64K method cap after B5. apply.bash invalidates
# the MultiDex code_cache so Dalvik picks up the new classes2.dex.
PATCH_B5_INJECT_FILES = [
    ("com/koensayr/y1/trackinfo/TrackInfoWriter.smali",
        "smali/com/koensayr/y1/trackinfo/TrackInfoWriter.smali"),
    ("com/koensayr/y1/playback/PlaybackStateBridge.smali",
        "smali/com/koensayr/y1/playback/PlaybackStateBridge.smali"),
    ("com/koensayr/y1/playback/PositionTicker.smali",
        "smali/com/koensayr/y1/playback/PositionTicker.smali"),
    ("com/koensayr/y1/battery/BatteryReceiver.smali",
        "smali/com/koensayr/y1/battery/BatteryReceiver.smali"),
    ("com/koensayr/y1/papp/PappSetFileObserver.smali",
        "smali/com/koensayr/y1/papp/PappSetFileObserver.smali"),
    ("com/koensayr/y1/ui/NowPlayingRefresher.smali",
        "smali/com/koensayr/y1/ui/NowPlayingRefresher.smali"),
]

# DEBUG-only Runnable for post-broadcast trampoline-state reads. Only included
# when KOENSAYR_DEBUG=1; release builds get no reference to this class anywhere
# (the calling _dbgPostReadAfter helper + the post-sendBroadcast invoke-static
# anchors are both gated on the same flag).
PATCH_B5_INJECT_FILES_DEBUG_ONLY = [
    ("com/koensayr/y1/trackinfo/DbgPostReadRunnable.smali",
        "smali/com/koensayr/y1/trackinfo/DbgPostReadRunnable.smali"),
]
if DEBUG_LOGGING:
    PATCH_B5_INJECT_FILES = PATCH_B5_INJECT_FILES + PATCH_B5_INJECT_FILES_DEBUG_ONLY

# --- DEBUG instrumentation for the inject tree (gated on KOENSAYR_DEBUG=1) ---
# Two layers:
#   1. Entry-point Log.d traces for every metadata-relevant method across all
#      inject smali files. Constant-string messages — answers "did this method
#      fire?" cheaply. Driven by PATCH_B5_DEBUG_ENTRY_TRACES.
#   2. Value-bearing inline _dbgKV calls at five diagnostic-critical sites
#      (TrackInfoWriter.onTrackEdge × 3, flushLocked summary, onSeek × 3,
#      setPlayStatus, PlaybackStateBridge.onPlayValue) — answers "with what
#      values?" Surfaces actual mCachedAudioId / position / duration /
#      play_status / seek_pos / suppression_decision in logcat.
# Helpers (_dbg, _dbgKV) are appended to TrackInfoWriter.smali only when
# DEBUG_LOGGING is true; release builds get the unmodified inject sources
# verbatim with zero runtime overhead.

DBG_HELPERS_SMALI = """\

# === DEBUG HELPERS (KOENSAYR_DEBUG=1; --debug) ===
# _dbg(String msg) → Log.d("Y1Patch", msg)
.method public static _dbg(Ljava/lang/String;)V
    .locals 1

    const-string v0, "Y1Patch"

    invoke-static {v0, p0}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method

# _dbgKV(String key, long val) → Log.d("Y1Patch", key + "=" + val)
.method public static _dbgKV(Ljava/lang/String;J)V
    .locals 3

    new-instance v0, Ljava/lang/StringBuilder;

    invoke-direct {v0}, Ljava/lang/StringBuilder;-><init>()V

    invoke-virtual {v0, p0}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string v1, "="

    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v0, p1, p2}, Ljava/lang/StringBuilder;->append(J)Ljava/lang/StringBuilder;

    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v0

    const-string v1, "Y1Patch"

    invoke-static {v1, v0}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method

# _dbgLogTrampolineState(String tag) → Log.d("Y1Patch", tag+" tramp.state[0..19]=HH HH …")
#
# Dumps the full 20-byte y1-trampoline-state file. Authoritative byte semantics
# from src/patches/_trampolines.py (T9_OFF_STATE = 8 in T9's stack frame, file
# layout matches):
#   bytes [0..7] = last-synced track_id (T5 mirror — compared against
#                  file[0..7] for TRACK_CHANGED edge detection)
#   byte  [8]    = (unused / pad)
#   byte  [9]    = last_play_status      — compare against fL.ps to detect
#                                          stale wire state
#   byte  [10]   = last_battery_status   — compare against fL.battery
#   byte  [11]   = last_repeat_avrcp     — papp edge
#   byte  [12]   = last_shuffle_avrcp    — papp edge
#   byte  [13]   = sub_pos_changed       (event 0x05 PLAYBACK_POS)
#   byte  [14]   = sub_play_status       (event 0x01 PLAYBACK_STATUS)
#   byte  [15]   = sub_papp              (event 0x08 PLAYER_APPLICATION_SETTING)
#   byte  [16]   = sub_track_changed     (event 0x02 TRACK_CHANGED)
#   byte  [17]   = sub_track_reached_end (event 0x03)
#   byte  [18]   = sub_track_reached_start (event 0x04)
#   byte  [19]   = sub_battery           (event 0x06 BATT_STATUS)
#   (byte [20]   = sub_now_playing_content event 0x09 — exists if file is 21 B,
#                  not dumped)
# Per architecture_y1_subscription_gating + Pixel-mirror gate semantics:
#   T2 / T8 INTERIM arms a gate byte = 1; T5 / T9 read + (some events) clear.
#
# Called immediately before each wakeTrackChanged / wakePlayStateChanged
# sendBroadcast() and once per PositionTicker tick. Successive captures answer:
#   * Was the gate armed (non-zero byte in [13..19]) when we kicked T5/T9?
#   * Did the previous wake actually clear the gate (proving T5/T9 emitted
#     CHANGED on the wire) or not (proving gate exhaustion: gate cleared on
#     a prior CHANGED, CT hasn't re-RegisterNotification'd yet)?
#   * Does the [0..12] mirror match the current y1-track-info (audio_id /
#     position / play_status)? Mismatch means T5/T9 hasn't published the
#     latest state to the wire yet — i.e., the icon-doesn't-update symptom.
.method public static _dbgLogTrampolineState(Ljava/lang/String;)V
    .locals 8

    :try_start_ts
    sget-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    iget-object v0, v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mFilesDir:Ljava/io/File;

    if-nez v0, :cond_have_dir

    return-void

    :cond_have_dir
    new-instance v1, Ljava/io/File;

    const-string v2, "y1-trampoline-state"

    invoke-direct {v1, v0, v2}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    new-instance v2, Ljava/io/FileInputStream;

    invoke-direct {v2, v1}, Ljava/io/FileInputStream;-><init>(Ljava/io/File;)V

    const/16 v3, 0x14

    new-array v3, v3, [B

    invoke-virtual {v2, v3}, Ljava/io/FileInputStream;->read([B)I

    move-result v4

    invoke-virtual {v2}, Ljava/io/FileInputStream;->close()V

    const/16 v5, 0x14

    if-lt v4, v5, :cond_short_read

    new-instance v5, Ljava/lang/StringBuilder;

    invoke-direct {v5}, Ljava/lang/StringBuilder;-><init>()V

    invoke-virtual {v5, p0}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string v6, " tramp.state[0..19]="

    invoke-virtual {v5, v6}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const/16 v4, 0x0

    :goto_loop
    const/16 v6, 0x14

    if-ge v4, v6, :cond_loop_done

    aget-byte v6, v3, v4

    and-int/lit16 v6, v6, 0xff

    invoke-static {v6}, Ljava/lang/Integer;->toHexString(I)Ljava/lang/String;

    move-result-object v6

    invoke-virtual {v5, v6}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string v6, " "

    invoke-virtual {v5, v6}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    add-int/lit8 v4, v4, 0x1

    goto :goto_loop

    :cond_loop_done
    invoke-virtual {v5}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v5

    const-string v6, "Y1Patch"

    invoke-static {v6, v5}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I

    :cond_short_read
    :try_end_ts
    .catch Ljava/lang/Throwable; {:try_start_ts .. :try_end_ts} :catch_ts

    return-void

    :catch_ts
    move-exception v0

    return-void
.end method

# _dbgPostReadAfter(String tag, long delayMs) → schedule a delayed
# trampoline-state dump on the main looper. Allocates a fresh Handler +
# DbgPostReadRunnable per call (debug-only path, allocation cost
# irrelevant). The Runnable logs "{tag}.post tramp.state[0..19]=…" so a
# tag like "wTC" produces "wTC.pre" (pre-broadcast, immediate) + "wTC.post"
# (post-broadcast, ~50 ms later) — together they reveal whether T5/T9
# emitted CHANGED on the wire (the relevant §6.7.1 gate byte cleared
# between pre and post) or got gated out (byte stayed armed).
.method public static _dbgPostReadAfter(Ljava/lang/String;J)V
    .locals 5

    :try_start_pa
    new-instance v0, Landroid/os/Handler;

    invoke-static {}, Landroid/os/Looper;->getMainLooper()Landroid/os/Looper;

    move-result-object v1

    invoke-direct {v0, v1}, Landroid/os/Handler;-><init>(Landroid/os/Looper;)V

    new-instance v1, Lcom/koensayr/y1/trackinfo/DbgPostReadRunnable;

    invoke-direct {v1, p0}, Lcom/koensayr/y1/trackinfo/DbgPostReadRunnable;-><init>(Ljava/lang/String;)V

    invoke-virtual {v0, v1, p1, p2}, Landroid/os/Handler;->postDelayed(Ljava/lang/Runnable;J)Z
    :try_end_pa
    .catch Ljava/lang/Throwable; {:try_start_pa .. :try_end_pa} :catch_pa

    return-void

    :catch_pa
    move-exception v0

    return-void
.end method
"""

PATCH_B5_DEBUG_ENTRY_TRACES = {
    # file (relative to INJECT_ROOT) → list of (method_signature_re, msg) tuples
    "com/koensayr/y1/trackinfo/TrackInfoWriter.smali": [
        (r'init\(Landroid/content/Context;\)V', "TrackInfoWriter.init"),
        (r'setPlayStatus\(B\)V',                "TrackInfoWriter.setPlayStatus entry"),
        (r'onSeek\(J\)V',                       "TrackInfoWriter.onSeek entry"),
        (r'markCompletion\(\)V',                "TrackInfoWriter.markCompletion"),
        (r'markError\(\)V',                     "TrackInfoWriter.markError"),
        (r'onFreshTrackChange\(\)V',            "TrackInfoWriter.onFreshTrackChange entry"),
        (r'onTrackEdge\(\)V',                   "TrackInfoWriter.onTrackEdge entry"),
        (r'setBattery\(B\)V',                   "TrackInfoWriter.setBattery entry"),
        (r'setPapp\(II\)V',                     "TrackInfoWriter.setPapp entry"),
        (r'flush\(\)V',                         "TrackInfoWriter.flush"),
        (r'flushLocked\(\)V',                   "TrackInfoWriter.flushLocked entry"),
        (r'wakeTrackChanged\(\)V',              "TrackInfoWriter.wakeTrackChanged"),
        (r'wakePlayStateChanged\(\)V',          "TrackInfoWriter.wakePlayStateChanged"),
    ],
    "com/koensayr/y1/playback/PlaybackStateBridge.smali": [
        (r'onPlayValue\(II\)V',                 "PlaybackStateBridge.onPlayValue entry"),
        (r'onEarlyTrackChange\(\)V',            "PlaybackStateBridge.onEarlyTrackChange"),
        (r'onPrepared\(\)V',                    "PlaybackStateBridge.onPrepared"),
        (r'onPlayerPreparedTail\(\)V',          "PlaybackStateBridge.onPlayerPreparedTail"),
        (r'onCompletion\(\)V',                  "PlaybackStateBridge.onCompletion"),
        (r'onSeek\(J\)V',                       "PlaybackStateBridge.onSeek entry"),
        (r'onError\(\)V',                       "PlaybackStateBridge.onError"),
    ],
    "com/koensayr/y1/playback/PositionTicker.smali": [
        (r'start\(\)V',                         "PositionTicker.start"),
        (r'stop\(\)V',                          "PositionTicker.stop"),
        (r'run\(\)V',                           "PositionTicker.run (1s tick)"),
    ],
    "com/koensayr/y1/battery/BatteryReceiver.smali": [
        (r'register\(Landroid/content/Context;\)V',
                                                "BatteryReceiver.register"),
        (r'onReceive\(Landroid/content/Context;Landroid/content/Intent;\)V',
                                                "BatteryReceiver.onReceive"),
    ],
    "com/koensayr/y1/papp/PappSetFileObserver.smali": [
        (r'start\(Landroid/content/Context;\)V', "PappSetFileObserver.start"),
        (r'onEvent\(ILjava/lang/String;\)V',     "PappSetFileObserver.onEvent"),
        (r'dispatch\(II\)V',                     "PappSetFileObserver.dispatch entry"),
    ],
    "com/koensayr/y1/ui/NowPlayingRefresher.smali": [
        (r'onResume\(Lcom/innioasis/music/MusicPlayerActivity;\)V',
                                                "NowPlayingRefresher.onResume"),
        (r'onPause\(Lcom/innioasis/music/MusicPlayerActivity;\)V',
                                                "NowPlayingRefresher.onPause"),
        (r'refresh\(\)V',                        "NowPlayingRefresher.refresh"),
        (r'run\(\)V',                            "NowPlayingRefresher.run"),
    ],
}

# Value-bearing inline patches. Each tuple is (anchor, replacement, label).
# Anchors are exact-match strings; the patcher errors out cleanly if any
# anchor is missing (so smali shape drift surfaces immediately, not as a
# silent no-instrumentation build).

DBG_VALUE_PATCHES_TRACKINFOWRITER = [
    # onTrackEdge: log oldAudioId before flushLocked (snapshot of mCachedAudioId
    # from prior flush — this is the "before" side of the edge dedup compare).
    (
        "    # Snapshot the previous cached audio_id (from prior flushLocked).\n"
        "    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J\n",
        "    # Snapshot the previous cached audio_id (from prior flushLocked).\n"
        "    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J\n"
        "\n"
        "    # === DEBUG: log oldAudioId ===\n"
        "    const-string v4, \"onTE.old\"\n"
        "    invoke-static {v4, v0, v1}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    # === END DEBUG ===\n",
        "onTrackEdge.oldAudioId",
    ),
    # onTrackEdge: log newAudioId after flushLocked (mCachedAudioId now holds
    # the just-recomputed value — this is the "after" side of the edge compare).
    (
        "    # Compare new audio_id (just written) with snapshot.\n"
        "    iget-wide v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J\n",
        "    # Compare new audio_id (just written) with snapshot.\n"
        "    iget-wide v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J\n"
        "\n"
        "    # === DEBUG: log newAudioId ===\n"
        "    const-string v4, \"onTE.new\"\n"
        "    invoke-static {v4, v2, v3}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    # === END DEBUG ===\n",
        "onTrackEdge.newAudioId",
    ),
    # onTrackEdge: log when the EDGE branch fires (audio_ids differed →
    # we'll reset mPositionAtStateChange to 0 + stamp mLastFreshTrackChangeAt).
    (
        "    if-eqz v4, :cond_same_track\n"
        "\n"
        "    # Real track edge — reset position anchor and re-flush.\n",
        "    if-eqz v4, :cond_same_track\n"
        "\n"
        "    # === DEBUG: edge detected ===\n"
        "    const-string v4, \"onTE.EDGE_DETECTED\"\n"
        "    invoke-static {v4}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbg(Ljava/lang/String;)V\n"
        "    # === END DEBUG ===\n"
        "\n"
        "    # Real track edge — reset position anchor and re-flush.\n",
        "onTrackEdge.EDGE",
    ),
    # flushLocked: 4-line summary just before the FileOutputStream write —
    # captures exactly what got written to y1-track-info this flush
    # (audio_id, mPositionAtStateChange, mLastKnownDuration, mPlayStatus).
    (
        "    invoke-static {v1, v0, v2, v7}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putUtf8Padded([BIILjava/lang/String;)V\n"
        "\n"
        "    # Atomic write to filesDir/y1-track-info.tmp -> rename to y1-track-info\n"
        "    new-instance v0, Ljava/io/File;\n",
        "    invoke-static {v1, v0, v2, v7}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putUtf8Padded([BIILjava/lang/String;)V\n"
        "\n"
        "    # === DEBUG: log final flush state ===\n"
        "    const-string v0, \"fL.id\"\n"
        "    iget-wide v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J\n"
        "    invoke-static {v0, v2, v3}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    const-string v0, \"fL.pos\"\n"
        "    iget-wide v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J\n"
        "    invoke-static {v0, v2, v3}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    const-string v0, \"fL.dur\"\n"
        "    iget-wide v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastKnownDuration:J\n"
        "    invoke-static {v0, v2, v3}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    const-string v0, \"fL.ps\"\n"
        "    iget-byte v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPlayStatus:B\n"
        "    int-to-long v2, v2\n"
        "    invoke-static {v0, v2, v3}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    # === END DEBUG ===\n"
        "\n"
        "    # Atomic write to filesDir/y1-track-info.tmp -> rename to y1-track-info\n"
        "    new-instance v0, Ljava/io/File;\n",
        "flushLocked.summary",
    ),
    # onSeek entry: log input position (pre-suppression check).
    (
        ".method public declared-synchronized onSeek(J)V\n"
        "    .locals 5\n"
        "\n"
        "    monitor-enter p0\n"
        "\n"
        "    :try_start_0\n"
        "    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastFreshTrackChangeAt:J\n",
        ".method public declared-synchronized onSeek(J)V\n"
        "    .locals 5\n"
        "\n"
        "    monitor-enter p0\n"
        "\n"
        "    :try_start_0\n"
        "    # === DEBUG: log seek input ===\n"
        "    const-string v4, \"onSeek.in\"\n"
        "    invoke-static {v4, p1, p2}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    # === END DEBUG ===\n"
        "\n"
        "    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastFreshTrackChangeAt:J\n",
        "onSeek.entry",
    ),
    # onSeek SUPPRESS branch: log when within-2s-of-fresh-track suppression
    # fires (so we can confirm the suppression hypothesis empirically — the
    # ms-since-fresh-track value v2/v3 is currently in scope).
    (
        "    if-gez v4, :cond_normal\n"
        "\n"
        "    # Within ~2 s of a fresh track-change reset — this seek is almost\n"
        "    # certainly playerPrepared's restore-from-saved-progress call.\n"
        "    # Skip the position update (and the wakePlayStateChanged broadcast,\n"
        "    # since nothing changed). Don't clear mLastFreshTrackChangeAt — if\n"
        "    # playerPrepared somehow fires a second restore call (e.g. for\n"
        "    # bookmark + progress) we want to suppress that too.\n"
        "    monitor-exit p0\n",
        "    if-gez v4, :cond_normal\n"
        "\n"
        "    # Within ~2 s of a fresh track-change reset — this seek is almost\n"
        "    # certainly playerPrepared's restore-from-saved-progress call.\n"
        "    # Skip the position update (and the wakePlayStateChanged broadcast,\n"
        "    # since nothing changed). Don't clear mLastFreshTrackChangeAt — if\n"
        "    # playerPrepared somehow fires a second restore call (e.g. for\n"
        "    # bookmark + progress) we want to suppress that too.\n"
        "    # === DEBUG: log suppression with dt ===\n"
        "    const-string v4, \"onSeek.SUPPRESSED.dtMs\"\n"
        "    invoke-static {v4, v2, v3}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    # === END DEBUG ===\n"
        "    monitor-exit p0\n",
        "onSeek.SUPPRESSED",
    ),
    # onSeek APPLIED branch: log when the seek actually updates the anchor.
    (
        "    :cond_normal\n"
        "    iput-wide p1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J\n",
        "    :cond_normal\n"
        "    # === DEBUG: log applied seek ===\n"
        "    const-string v4, \"onSeek.APPLIED.pos\"\n"
        "    invoke-static {v4, p1, p2}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    # === END DEBUG ===\n"
        "    iput-wide p1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J\n",
        "onSeek.APPLIED",
    ),
    # setPlayStatus entry: log from→to play_status transition (pre-dedup).
    (
        ".method public declared-synchronized setPlayStatus(B)V\n"
        "    .locals 7\n"
        "\n"
        "    monitor-enter p0\n"
        "\n"
        "    :try_start_0\n"
        "    iget-byte v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPlayStatus:B\n",
        ".method public declared-synchronized setPlayStatus(B)V\n"
        "    .locals 5\n"
        "\n"
        "    monitor-enter p0\n"
        "\n"
        "    :try_start_0\n"
        "    # === DEBUG: log play-status transition ===\n"
        "    iget-byte v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPlayStatus:B\n"
        "    int-to-long v2, v0\n"
        "    const-string v4, \"sPS.from\"\n"
        "    invoke-static {v4, v2, v3}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    int-to-long v2, p1\n"
        "    const-string v4, \"sPS.to\"\n"
        "    invoke-static {v4, v2, v3}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    # === END DEBUG ===\n"
        "    iget-byte v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPlayStatus:B\n",
        "setPlayStatus.entry",
    ),
    # wakeTrackChanged pre-broadcast: dump trampoline-state[13..19] so we can
    # see the §6.7.1 gates (especially state[13]=TRACK_CHANGED) at the moment
    # we kick T5. If it stays armed across two consecutive wakes, T5 didn't
    # emit between them (gated out, no re-RegisterNotification from CT yet).
    (
        ".method public wakeTrackChanged()V\n"
        "    .locals 5\n"
        "\n"
        "    :try_start_0\n"
        "    iget-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mContext:Landroid/content/Context;\n",
        ".method public wakeTrackChanged()V\n"
        "    .locals 5\n"
        "\n"
        "    :try_start_0\n"
        "    # === DEBUG: log trampoline-state pre-broadcast ===\n"
        "    const-string v0, \"wTC.pre\"\n"
        "    invoke-static {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgLogTrampolineState(Ljava/lang/String;)V\n"
        "    # === END DEBUG ===\n"
        "    iget-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mContext:Landroid/content/Context;\n",
        "wakeTrackChanged.preBroadcast",
    ),
    # wakePlayStateChanged pre-broadcast: same diagnostic for state[14]
    # (PLAYBACK_STATUS) / state[15] (POS) / state[16] (BATT) / state[17]
    # (PAPP) — the four T9-emitted gates. Critical for diagnosing the
    # play/pause icon non-update issue: if state[14] stays non-zero across
    # successive wakes, T9 isn't emitting PLAYBACK_STATUS_CHANGED on the
    # wire (likely because gate cleared from a prior wake, CT hasn't
    # re-RegisterNotification'd yet).
    (
        ".method public wakePlayStateChanged()V\n"
        "    .locals 5\n"
        "\n"
        "    :try_start_0\n"
        "    iget-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mContext:Landroid/content/Context;\n",
        ".method public wakePlayStateChanged()V\n"
        "    .locals 5\n"
        "\n"
        "    :try_start_0\n"
        "    # === DEBUG: log trampoline-state pre-broadcast ===\n"
        "    const-string v0, \"wPSC.pre\"\n"
        "    invoke-static {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgLogTrampolineState(Ljava/lang/String;)V\n"
        "    # === END DEBUG ===\n"
        "    iget-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mContext:Landroid/content/Context;\n",
        "wakePlayStateChanged.preBroadcast",
    ),
    # wakeTrackChanged post-broadcast: schedule a ~50 ms delayed
    # trampoline-state read so we can compare pre vs post and answer
    # definitively whether T5 emitted TRACK_CHANGED CHANGED on the wire
    # (state[13] cleared between pre and post) or got gated out (byte
    # stayed armed). Anchored on the "album" putExtra line that's unique
    # to wakeTrackChanged.
    (
        "    const-string v2, \"album\"\n"
        "\n"
        "    iget-object v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAlbum:Ljava/lang/String;\n"
        "\n"
        "    invoke-virtual {v1, v2, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;\n"
        "\n"
        "    invoke-virtual {v0, v1}, Landroid/content/Context;->sendBroadcast(Landroid/content/Intent;)V\n"
        "\n"
        "    :cond_no_ctx\n",
        "    const-string v2, \"album\"\n"
        "\n"
        "    iget-object v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAlbum:Ljava/lang/String;\n"
        "\n"
        "    invoke-virtual {v1, v2, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;\n"
        "\n"
        "    invoke-virtual {v0, v1}, Landroid/content/Context;->sendBroadcast(Landroid/content/Intent;)V\n"
        "\n"
        "    # === DEBUG: schedule post-broadcast trampoline-state read ===\n"
        "    const-string v2, \"wTC\"\n"
        "    const-wide/16 v3, 0x32\n"
        "    invoke-static {v2, v3, v4}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgPostReadAfter(Ljava/lang/String;J)V\n"
        "    # === END DEBUG ===\n"
        "\n"
        "    :cond_no_ctx\n",
        "wakeTrackChanged.postBroadcast",
    ),
    # wakePlayStateChanged post-broadcast: same diagnostic. Anchored on
    # the "playing" boolean branch unique to wakePlayStateChanged.
    (
        "    :cond_playing\n"
        "    invoke-virtual {v1, v2, v4}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Z)Landroid/content/Intent;\n"
        "\n"
        "    invoke-virtual {v0, v1}, Landroid/content/Context;->sendBroadcast(Landroid/content/Intent;)V\n"
        "\n"
        "    :cond_no_ctx\n",
        "    :cond_playing\n"
        "    invoke-virtual {v1, v2, v4}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Z)Landroid/content/Intent;\n"
        "\n"
        "    invoke-virtual {v0, v1}, Landroid/content/Context;->sendBroadcast(Landroid/content/Intent;)V\n"
        "\n"
        "    # === DEBUG: schedule post-broadcast trampoline-state read ===\n"
        "    const-string v2, \"wPSC\"\n"
        "    const-wide/16 v3, 0x32\n"
        "    invoke-static {v2, v3, v4}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgPostReadAfter(Ljava/lang/String;J)V\n"
        "    # === END DEBUG ===\n"
        "\n"
        "    :cond_no_ctx\n",
        "wakePlayStateChanged.postBroadcast",
    ),
]

DBG_VALUE_PATCHES_PLAYBACKSTATEBRIDGE = [
    # onPlayValue entry: log raw newValue + reason ints (pre-mapping).
    (
        ".method public static onPlayValue(II)V\n"
        "    .locals 3\n"
        "\n"
        "    :try_start_b5\n"
        "    const/4 v0, -0x1\n",
        ".method public static onPlayValue(II)V\n"
        "    .locals 3\n"
        "\n"
        "    :try_start_b5\n"
        "    # === DEBUG: log new play-value + reason ===\n"
        "    int-to-long v0, p0\n"
        "    const-string v2, \"oPV.newVal\"\n"
        "    invoke-static {v2, v0, v1}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    int-to-long v0, p1\n"
        "    const-string v2, \"oPV.reason\"\n"
        "    invoke-static {v2, v0, v1}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgKV(Ljava/lang/String;J)V\n"
        "    # === END DEBUG ===\n"
        "    const/4 v0, -0x1\n",
        "onPlayValue.entry",
    ),
]


def _apply_b5_dbg_value_patches(smali, patches, file_label):
    """Run a list of (anchor, replacement, label) value-bearing patches.

    Each patch is exact-string anchor → replacement. Errors out cleanly
    on missing anchor so smali shape drift surfaces immediately rather
    than as a silent no-instrumentation build.
    """
    for anchor, replacement, label in patches:
        if anchor not in smali:
            sys.exit(
                f"ERROR: --debug value patch anchor missing in {file_label}: {label!r}"
            )
        if replacement in smali:
            continue  # idempotent
        smali = smali.replace(anchor, replacement, 1)
    return smali


def _apply_b5_dbg_instrumentation(src_rel, smali):
    """Apply entry-trace + value-bearing instrumentation for one inject smali.

    Steps:
      1. Append _dbg / _dbgKV helper methods (TrackInfoWriter only — other
         inject files reach helpers via cross-class invoke-static).
      2. Apply value-bearing patches (TrackInfoWriter, PlaybackStateBridge).
      3. Inject entry-point Log.d traces for every method in
         PATCH_B5_DEBUG_ENTRY_TRACES[src_rel].

    Order matters: helpers must be appended before invoke-static calls
    referencing them, and value-bearing patches must run before entry-traces
    so the entry-trace insertion (which sits between .locals and the first
    instruction) doesn't shift line offsets that value-patch anchors depend
    on.
    """
    if src_rel == "com/koensayr/y1/trackinfo/TrackInfoWriter.smali":
        if DBG_HELPERS_SMALI not in smali:
            smali = smali.rstrip() + "\n" + DBG_HELPERS_SMALI
        smali = _apply_b5_dbg_value_patches(
            smali, DBG_VALUE_PATCHES_TRACKINFOWRITER, src_rel
        )
    elif src_rel == "com/koensayr/y1/playback/PlaybackStateBridge.smali":
        smali = _apply_b5_dbg_value_patches(
            smali, DBG_VALUE_PATCHES_PLAYBACKSTATEBRIDGE, src_rel
        )

    for sig, msg in PATCH_B5_DEBUG_ENTRY_TRACES.get(src_rel, []):
        smali = _inject_log_d(smali, sig, msg)
    return smali


for src_rel, dst_rel in PATCH_B5_INJECT_FILES:
    src = os.path.join(INJECT_ROOT, src_rel)
    dst = os.path.join(UNPACKED_DIR, dst_rel)
    if not os.path.exists(src):
        sys.exit(f"ERROR: Patch B5 source missing: {src}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if DEBUG_LOGGING:
        with open(src, 'r') as f:
            smali = f.read()
        smali = _apply_b5_dbg_instrumentation(src_rel, smali)
        with open(dst, 'w') as f:
            f.write(smali)
        n_traces = len(PATCH_B5_DEBUG_ENTRY_TRACES.get(src_rel, []))
        print(f"  Wrote {dst_rel}  (+{n_traces} entry traces; --debug)")
    else:
        shutil.copyfile(src, dst)
        print(f"  Wrote {dst_rel}")

# -- Patch B5.1: hook Static.setPlayValue -------------------------------------
STATIC_SMALI = "smali_classes2/com/innioasis/y1/utils/Static.smali"
static_path = os.path.join(UNPACKED_DIR, STATIC_SMALI)
if not os.path.exists(static_path):
    sys.exit(f"ERROR: Static.smali not found: {static_path}")
with open(static_path, 'r') as f:
    static_src = f.read()

OLD_SET_PLAY_VALUE_HEAD = (
    ".method public final setPlayValue(II)V\n"
    "    .locals 5\n"
    "\n"
    "    .line 49\n"
    "    sget-object v0, Lcom/innioasis/y1/utils/Static;->mPlayValue:Landroidx/lifecycle/MutableLiveData;\n"
)
NEW_SET_PLAY_VALUE_HEAD = (
    ".method public final setPlayValue(II)V\n"
    "    .locals 5\n"
    "\n"
    "    invoke-static {p1, p2}, Lcom/koensayr/y1/playback/PlaybackStateBridge;->onPlayValue(II)V\n"
    "\n"
    "    .line 49\n"
    "    sget-object v0, Lcom/innioasis/y1/utils/Static;->mPlayValue:Landroidx/lifecycle/MutableLiveData;\n"
)
if OLD_SET_PLAY_VALUE_HEAD not in static_src:
    sys.exit("ERROR: Patch B5.1 anchor not found in Static.smali (setPlayValue header).")
static_src = static_src.replace(OLD_SET_PLAY_VALUE_HEAD, NEW_SET_PLAY_VALUE_HEAD, 1)
with open(static_path, 'w') as f:
    f.write(static_src)
print(f"  Patch B5.1: Static.setPlayValue → PlaybackStateBridge.onPlayValue")

# -- Patch B5.2: hook PlayerService listener lambdas --------------------------
# Six prepends — three per engine (IJK + MediaPlayer). Each lambda has a stable
# header pattern (.method ... ; .locals N ; const-string p1, "this$0" ;
# invoke-static checkNotNullParameter). We anchor on the first three lines to
# uniquely identify each lambda even though the body varies.
#
# Lambda identity (verified via $r8$lambda$* accessor chain — see
# docs/RECON-MUSIC-APP-HOOKS.md §3):
#   initPlayer$lambda-10  → IjkMediaPlayer OnCompletionListener
#   initPlayer$lambda-11  → IjkMediaPlayer OnPreparedListener
#   initPlayer$lambda-12  → IjkMediaPlayer OnErrorListener
#   initPlayer2$lambda-13 → MediaPlayer    OnCompletionListener
#   initPlayer2$lambda-14 → MediaPlayer    OnPreparedListener
#   initPlayer2$lambda-15 → MediaPlayer    OnErrorListener

PLAYER_SERVICE_SMALI_FOR_B5 = "smali/com/innioasis/y1/service/PlayerService.smali"
ps_path = os.path.join(UNPACKED_DIR, PLAYER_SERVICE_SMALI_FOR_B5)
if not os.path.exists(ps_path):
    sys.exit(f"ERROR: PlayerService.smali not found: {ps_path}")
with open(ps_path, 'r') as f:
    ps_src = f.read()

PATCH_B5_LAMBDA_HOOKS = [
    # (lambda method name, target callback)
    ("initPlayer$lambda-10",  "onCompletion"),
    ("initPlayer$lambda-11",  "onPrepared"),
    ("initPlayer$lambda-12",  "onError"),
    ("initPlayer2$lambda-13", "onCompletion"),
    ("initPlayer2$lambda-14", "onPrepared"),
    ("initPlayer2$lambda-15", "onError"),
]

for lname, callback in PATCH_B5_LAMBDA_HOOKS:
    # Locate the method declaration line; insert immediately after .locals.
    needle_method = f".method private static final {lname}("
    idx = ps_src.find(needle_method)
    if idx < 0:
        sys.exit(f"ERROR: Patch B5.2 anchor not found: {lname}")
    # Find the .locals line after the method declaration.
    locals_idx = ps_src.find("\n    .locals ", idx)
    if locals_idx < 0 or locals_idx > idx + 200:
        sys.exit(f"ERROR: Patch B5.2 .locals not found near {lname}")
    line_end = ps_src.find("\n", locals_idx + 1)
    inject = (
        f"\n\n    invoke-static {{}}, "
        f"Lcom/koensayr/y1/playback/PlaybackStateBridge;->{callback}()V"
    )
    # Idempotency guard so a re-run doesn't double-prepend.
    if ps_src[line_end:line_end + len(inject)] == inject:
        continue
    ps_src = ps_src[:line_end] + inject + ps_src[line_end:]

with open(ps_path, 'w') as f:
    f.write(ps_src)
print(f"  Patch B5.2: PlayerService 6 listener lambdas → PlaybackStateBridge")

# -- Patch B5.2a: hook PlayerService.setCurrentPosition (seek edge) -----------
# The music app doesn't register OnSeekCompleteListener on either engine, so
# we hook the seek call directly. setCurrentPosition(J) is the single public
# entry from BasePlayerActivity's seek-bar handler and PlayerService's own
# internal repositioning paths — every seek funnels through it before the
# call to IjkMediaPlayer.seekTo / MediaPlayer.seekTo.
OLD_SET_CUR_POS_HEAD = (
    ".method public final setCurrentPosition(J)V\n"
    "    .locals 2\n"
    "\n"
    "    .line 171\n"
    "    iget-object v0, p0, Lcom/innioasis/y1/service/PlayerService;->playing:Lcom/innioasis/y1/service/PlayerService$Playing;\n"
)
NEW_SET_CUR_POS_HEAD = (
    ".method public final setCurrentPosition(J)V\n"
    "    .locals 2\n"
    "\n"
    "    invoke-static {p1, p2}, Lcom/koensayr/y1/playback/PlaybackStateBridge;->onSeek(J)V\n"
    "\n"
    "    .line 171\n"
    "    iget-object v0, p0, Lcom/innioasis/y1/service/PlayerService;->playing:Lcom/innioasis/y1/service/PlayerService$Playing;\n"
)
if OLD_SET_CUR_POS_HEAD not in ps_src:
    sys.exit("ERROR: Patch B5.2a anchor not found in PlayerService.smali (setCurrentPosition header).")
ps_src = ps_src.replace(OLD_SET_CUR_POS_HEAD, NEW_SET_CUR_POS_HEAD, 1)
with open(ps_path, 'w') as f:
    f.write(ps_src)
print(f"  Patch B5.2a: PlayerService.setCurrentPosition → PlaybackStateBridge.onSeek")

# -- Patch B5.2b: pre-emit TRACK_CHANGED before prepareAsync completes --------
# PlayerService.toRestart() contains three setDataSource(newPath) call sites —
# one for the audiobook IJK path, one for the music IJK path, and one for the
# music AOSP MediaPlayer path. Each is followed by prepareAsync() (~100-500 ms
# of decoder warmup) and only then by OnPreparedListener, which is where our
# existing B5.2 hook fires onTrackEdge + wakeTrackChanged. By the time
# setDataSource has been called, mPlayingMusic / mPlayingAudiobook is the new
# song — flushLocked() reads the metadata from PlayerService.getPlayingSong()
# at that moment and writes it to y1-track-info, so we can fire the
# track-change notification immediately.
#
# Inject `invoke-static onEarlyTrackChange` after each setDataSource. The new
# helper in PlaybackStateBridge calls TrackInfoWriter.onTrackEdge() (which
# dedups by audio_id — same-track invocations like resume-from-pause refresh
# duration without disturbing the live-position baseline) and
# wakeTrackChanged() (broadcasts metachanged → MMI_AVRCP → T5). Saves up to
# ~500 ms of perceived metadata-refresh latency on track skip.
#
# Anchors: each setDataSource call is uniquely identified by the receiver
# register (v2 / v0 / v4) and the .line marker that follows it. The 3 sites
# are the only setDataSource calls in PlayerService.smali — verified by
# `grep -n setDataSource`.
TO_RESTART_HOOKS = [
    # (anchor, replacement) tuples for each setDataSource site in toRestart
    (
        # Site 1: audiobook branch (cond_0), IJK engine, receiver = v2.
        "    invoke-virtual {v2, v1}, Ltv/danmaku/ijk/media/player/IjkMediaPlayer;->setDataSource(Ljava/lang/String;)V\n"
        "\n"
        "    .line 551\n",
        "    invoke-virtual {v2, v1}, Ltv/danmaku/ijk/media/player/IjkMediaPlayer;->setDataSource(Ljava/lang/String;)V\n"
        "\n"
        "    invoke-static {}, Lcom/koensayr/y1/playback/PlaybackStateBridge;->onEarlyTrackChange()V\n"
        "\n"
        "    .line 551\n",
    ),
    (
        # Site 2: music branch (cond_2), IJK engine, receiver = v0.
        "    invoke-virtual {v0, v1}, Ltv/danmaku/ijk/media/player/IjkMediaPlayer;->setDataSource(Ljava/lang/String;)V\n"
        "\n"
        "    .line 528\n",
        "    invoke-virtual {v0, v1}, Ltv/danmaku/ijk/media/player/IjkMediaPlayer;->setDataSource(Ljava/lang/String;)V\n"
        "\n"
        "    invoke-static {}, Lcom/koensayr/y1/playback/PlaybackStateBridge;->onEarlyTrackChange()V\n"
        "\n"
        "    .line 528\n",
    ),
    (
        # Site 3: music branch (cond_2), AOSP MediaPlayer engine, receiver = v4.
        "    invoke-virtual {v4, v1}, Landroid/media/MediaPlayer;->setDataSource(Ljava/lang/String;)V\n"
        "\n"
        "    .line 535\n",
        "    invoke-virtual {v4, v1}, Landroid/media/MediaPlayer;->setDataSource(Ljava/lang/String;)V\n"
        "\n"
        "    invoke-static {}, Lcom/koensayr/y1/playback/PlaybackStateBridge;->onEarlyTrackChange()V\n"
        "\n"
        "    .line 535\n",
    ),
]
for i, (anchor, replacement) in enumerate(TO_RESTART_HOOKS, 1):
    if anchor not in ps_src:
        sys.exit(f"ERROR: Patch B5.2b anchor #{i} not found in PlayerService.smali "
                 f"(setDataSource site in toRestart). The anchor expects an exact "
                 f"match including the trailing .line marker.")
    if replacement in ps_src:
        continue  # idempotency: already patched
    ps_src = ps_src.replace(anchor, replacement, 1)
with open(ps_path, 'w') as f:
    f.write(ps_src)
print(f"  Patch B5.2b: PlayerService.toRestart × 3 setDataSource sites → PlaybackStateBridge.onEarlyTrackChange")

# -- Patch B5.2c: hook PlayerService.playerPrepared() tail ------------------
# Fires PlaybackStateBridge.onPlayerPreparedTail() after each
# `iput-boolean playerIsPrepared = true` in playerPrepared(). At that point
# getPlayerIsPrepared() returns true, so a fresh flushLocked captures the
# newly-valid getDuration() value; without this hook, flushLocked from
# OnPreparedListener runs ~26 ms BEFORE the flag flips and falls back to
# the prior track's stale mLastKnownDuration (verified Kia 2026-05-14:
# duration leaks one track late on every skip).
#
# playerPrepared() has TWO `iput-boolean … playerIsPrepared:Z` sites: one
# in the shutdown-restore branch (runs play / pause / setCurrentPosition
# right after) and one at the end of the normal-prepare branch. Hook both
# so duration capture works on every prepare regardless of which branch
# runs.
PLAYER_PREPARED_TAIL_HOOKS = [
    (
        # Site 1: shutdown-restore branch — followed by `.line 982` marker.
        "    .line 981\n"
        "    iput-boolean v5, p0, Lcom/innioasis/y1/service/PlayerService;->playerIsPrepared:Z\n"
        "\n"
        "    .line 982\n",
        "    .line 981\n"
        "    iput-boolean v5, p0, Lcom/innioasis/y1/service/PlayerService;->playerIsPrepared:Z\n"
        "\n"
        "    invoke-static {}, Lcom/koensayr/y1/playback/PlaybackStateBridge;->onPlayerPreparedTail()V\n"
        "\n"
        "    .line 982\n",
    ),
    (
        # Site 2: normal-prepare branch — followed by `return-void` at method end.
        "    .line 1035\n"
        "    iput-boolean v5, p0, Lcom/innioasis/y1/service/PlayerService;->playerIsPrepared:Z\n"
        "\n"
        "    return-void\n",
        "    .line 1035\n"
        "    iput-boolean v5, p0, Lcom/innioasis/y1/service/PlayerService;->playerIsPrepared:Z\n"
        "\n"
        "    invoke-static {}, Lcom/koensayr/y1/playback/PlaybackStateBridge;->onPlayerPreparedTail()V\n"
        "\n"
        "    return-void\n",
    ),
]
for i, (anchor, replacement) in enumerate(PLAYER_PREPARED_TAIL_HOOKS, 1):
    if anchor not in ps_src:
        sys.exit(f"ERROR: Patch B5.2c anchor #{i} not found in PlayerService.smali "
                 f"(playerIsPrepared:=true site in playerPrepared). The anchor "
                 f"expects an exact match including the .line markers.")
    if replacement in ps_src:
        continue  # idempotency: already patched
    ps_src = ps_src.replace(anchor, replacement, 1)
with open(ps_path, 'w') as f:
    f.write(ps_src)
print(f"  Patch B5.2c: PlayerService.playerPrepared × 2 playerIsPrepared:=true sites → PlaybackStateBridge.onPlayerPreparedTail")

# -- Patch B5.3: extend Y1Application.onCreate registration block -------------
# Insert BEFORE the B4 PappStateBroadcaster registration so TrackInfoWriter is
# initialised by the time sendNow() runs (sendNow → B5.4 setPapp → flushLocked
# would no-op if mFilesDir was null). New order:
#   B3 receiver register → B5 (TrackInfoWriter init + observers) → B4 broadcaster
with open(y1app_path, 'r') as f:
    y1app_src = f.read()

OLD_Y1APP_B4_HEAD = (
    "    # Patch B4: register PappStateBroadcaster as OnSharedPreferenceChangeListener"
)
NEW_Y1APP_B4_HEAD = (
    "    # Patch B5: in-app y1-track-info production. Init TrackInfoWriter\n"
    "    # (creates filesDir + watched files), then start observers. Order\n"
    "    # matters: must run before B4 sendNow so the first file write\n"
    "    # reflects the live SharedPreferences state.\n"
    "    sget-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;\n"
    "\n"
    "    invoke-virtual {v0, p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->init(Landroid/content/Context;)V\n"
    "\n"
    "    invoke-static {p0}, Lcom/koensayr/y1/papp/PappSetFileObserver;->start(Landroid/content/Context;)V\n"
    "\n"
    "    invoke-static {p0}, Lcom/koensayr/y1/battery/BatteryReceiver;->register(Landroid/content/Context;)V\n"
    "\n"
    "    # Patch B4: register PappStateBroadcaster as OnSharedPreferenceChangeListener"
)
if OLD_Y1APP_B4_HEAD not in y1app_src:
    sys.exit("ERROR: Patch B5.3 anchor not found (B4 head comment).")
y1app_src = y1app_src.replace(OLD_Y1APP_B4_HEAD, NEW_Y1APP_B4_HEAD, 1)
if DEBUG_LOGGING:
    y1app_src = _inject_log_d(
        y1app_src,
        r'onCreate\(\)V',
        "Y1Application.onCreate entry",
    )
with open(y1app_path, 'w') as f:
    f.write(y1app_src)
print(f"  Patch B5.3: Y1Application.onCreate registers TrackInfoWriter / "
      f"PappSetFileObserver / BatteryReceiver (before B4 sendNow)"
      f"{' [+1 entry trace; --debug]' if DEBUG_LOGGING else ''}")

# -- Patch B5.4: extend PappStateBroadcaster.sendNow ---------------------------
# After the PAPP_STATE_DID_CHANGE broadcast we (a) call
# TrackInfoWriter.setPapp(repeat, shuffle) so the music-app's
# y1-track-info[795..796] bytes reflect the new state immediately and
# (b) fire `com.android.music.playstatechanged` so MtkBt's
# BluetoothAvrcpReceiver wakes notificationPlayStatusChangedNative → T9 →
# AVRCP §5.4.2 Tbl 5.36 PLAYER_APPLICATION_SETTING_CHANGED CHANGED on the
# wire. The legacy PAPP_STATE_DID_CHANGE broadcast is retained but inert —
# Y1Bridge.apk no longer has a receiver for it.
OLD_PAPP_BCAST_TAIL = (
    "    invoke-virtual {v3, v2}, Landroid/content/Context;->sendBroadcast(Landroid/content/Intent;)V\n"
    "\n"
    "    return-void\n"
    ".end method\n"
    "\n"
    "\n"
    "# virtual methods — OnSharedPreferenceChangeListener"
)
NEW_PAPP_BCAST_TAIL = (
    "    invoke-virtual {v3, v2}, Landroid/content/Context;->sendBroadcast(Landroid/content/Intent;)V\n"
    "\n"
    "    # Patch B5.4: push live values into in-app TrackInfoWriter, then fire\n"
    "    # `com.android.music.playstatechanged` to wake T9 via MtkBt. Wrapped in\n"
    "    # try/catch(Throwable) so a writer-side bug cannot propagate into the\n"
    "    # SharedPreferences listener notification chain or the cold-boot\n"
    "    # Y1Application.onCreate sendNow().\n"
    "    :try_start_b5_setpapp\n"
    "    sget-object v2, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;\n"
    "\n"
    "    invoke-virtual {v2, v0, v1}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->setPapp(II)V\n"
    "\n"
    "    new-instance v2, Landroid/content/Intent;\n"
    "\n"
    "    const-string v0, \"com.android.music.playstatechanged\"\n"
    "\n"
    "    invoke-direct {v2, v0}, Landroid/content/Intent;-><init>(Ljava/lang/String;)V\n"
    "\n"
    "    iget-object v0, p0, Lcom/koensayr/PappStateBroadcaster;->mContext:Landroid/content/Context;\n"
    "\n"
    "    invoke-virtual {v0, v2}, Landroid/content/Context;->sendBroadcast(Landroid/content/Intent;)V\n"
    "\n"
    "    # Patch B5.4a: refresh MusicPlayerActivity if it's visible so the\n"
    "    # in-app Now Playing screen reflects the new Repeat / Shuffle state\n"
    "    # without requiring a back-out / re-enter. Mirrors what AOSP\n"
    "    # MediaSession callbacks do for spec-compliant players.\n"
    "    invoke-static {}, Lcom/koensayr/y1/ui/NowPlayingRefresher;->refresh()V\n"
    "\n"
    "    :try_end_b5_setpapp\n"
    "    .catch Ljava/lang/Throwable; {:try_start_b5_setpapp .. :try_end_b5_setpapp} :catch_b5_setpapp\n"
    "\n"
    "    return-void\n"
    "\n"
    "    :catch_b5_setpapp\n"
    "    move-exception v2\n"
    "\n"
    "    return-void\n"
    ".end method\n"
    "\n"
    "\n"
    "# virtual methods — OnSharedPreferenceChangeListener"
)
with open(papp_broadcaster_path, 'r') as f:
    papp_bcast_src = f.read()
if OLD_PAPP_BCAST_TAIL not in papp_bcast_src:
    sys.exit("ERROR: Patch B5.4 anchor not found (sendNow tail of PappStateBroadcaster).")
papp_bcast_src = papp_bcast_src.replace(OLD_PAPP_BCAST_TAIL, NEW_PAPP_BCAST_TAIL, 1)
with open(papp_broadcaster_path, 'w') as f:
    f.write(papp_bcast_src)
print(f"  Patch B5.4: PappStateBroadcaster.sendNow → also TrackInfoWriter.setPapp + playstatechanged")

# -- Patch B5.5: hook MusicPlayerActivity onResume/onPause for UI refresh ----
# Track the currently visible Now Playing screen so PappStateBroadcaster can
# trigger a refreshUI() when Repeat / Shuffle changes (CT-driven or in-app).
# Without this, CT-driven changes apply to SharedPreferences but the visible
# UI doesn't re-render until the user navigates away and back.
MUSIC_PLAYER_ACTIVITY_SMALI = "smali_classes2/com/innioasis/music/MusicPlayerActivity.smali"
mpa_path = os.path.join(UNPACKED_DIR, MUSIC_PLAYER_ACTIVITY_SMALI)
if not os.path.exists(mpa_path):
    sys.exit(f"ERROR: MusicPlayerActivity.smali not found: {mpa_path}")
with open(mpa_path, 'r') as f:
    mpa_src = f.read()

OLD_MPA_ON_RESUME_HEAD = (
    ".method protected onResume()V\n"
    "    .locals 3\n"
    "\n"
    "    .line 108\n"
    "    invoke-super {p0}, Lcom/innioasis/y1/base/BasePlayerActivity;->onResume()V\n"
)
NEW_MPA_ON_RESUME_HEAD = (
    ".method protected onResume()V\n"
    "    .locals 3\n"
    "\n"
    "    invoke-static {p0}, Lcom/koensayr/y1/ui/NowPlayingRefresher;->onResume(Lcom/innioasis/music/MusicPlayerActivity;)V\n"
    "\n"
    "    .line 108\n"
    "    invoke-super {p0}, Lcom/innioasis/y1/base/BasePlayerActivity;->onResume()V\n"
)
if OLD_MPA_ON_RESUME_HEAD not in mpa_src:
    sys.exit("ERROR: Patch B5.5 anchor not found in MusicPlayerActivity.smali (onResume header).")
mpa_src = mpa_src.replace(OLD_MPA_ON_RESUME_HEAD, NEW_MPA_ON_RESUME_HEAD, 1)

# MusicPlayerActivity doesn't override onPause (inherits from BasePlayerActivity).
# Inject a minimal onPause that notifies the refresher then chains to super.
MPA_ON_PAUSE_INJECT = """

.method protected onPause()V
    .locals 0

    invoke-static {p0}, Lcom/koensayr/y1/ui/NowPlayingRefresher;->onPause(Lcom/innioasis/music/MusicPlayerActivity;)V

    invoke-super {p0}, Lcom/innioasis/y1/base/BasePlayerActivity;->onPause()V

    return-void
.end method
"""
# Idempotency: skip if already injected.
if "invoke-static {p0}, Lcom/koensayr/y1/ui/NowPlayingRefresher;->onPause" not in mpa_src:
    # Append before the final closing of the file (smali files don't have an
    # end marker; appending after the last .end method is sufficient).
    mpa_src = mpa_src.rstrip() + MPA_ON_PAUSE_INJECT + "\n"

# Inject refreshRepeatShuffleUi() — re-renders ONLY the Repeat / Shuffle
# ImageView icons from current SharedPreferences. Used by
# NowPlayingRefresher.run() to propagate CT-driven Repeat / Shuffle
# changes to the Now Playing screen without the side effects of re-
# running initView() (finish() on no-music, setSpeed(1.0f) reset).
# Resource IDs / SharedPreferences key mapping mirror initView()'s logic:
#   isShuffle TRUE  → R 0x7f0e002a   FALSE → R 0x7f0e0027
#   musicRepeatMode == 0 OFF    → R 0x7f0e0026
#   musicRepeatMode == 1 SINGLE → R 0x7f0e0029
#   musicRepeatMode == 2 ALL    → R 0x7f0e0028
MPA_REFRESH_PAPP_INJECT = """

.method public refreshRepeatShuffleUi()V
    .locals 3

    :try_start_0
    sget-object v0, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->INSTANCE:Lcom/innioasis/y1/utils/SharedPreferencesUtils;

    invoke-virtual {v0}, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->getMusicIsShuffle()Z

    move-result v0

    invoke-virtual {p0}, Lcom/innioasis/music/MusicPlayerActivity;->getVb()Landroidx/viewbinding/ViewBinding;

    move-result-object v1

    check-cast v1, Lcom/innioasis/y1/databinding/ActivityMusicPlayerBinding;

    iget-object v1, v1, Lcom/innioasis/y1/databinding/ActivityMusicPlayerBinding;->isShuffle:Landroid/widget/ImageView;

    if-eqz v0, :cond_shuffle_off

    const v2, 0x7f0e002a

    goto :goto_shuffle_set

    :cond_shuffle_off
    const v2, 0x7f0e0027

    :goto_shuffle_set
    invoke-virtual {v1, v2}, Landroid/widget/ImageView;->setImageResource(I)V

    sget-object v0, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->INSTANCE:Lcom/innioasis/y1/utils/SharedPreferencesUtils;

    invoke-virtual {v0}, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->getMusicRepeatMode()I

    move-result v0

    invoke-virtual {p0}, Lcom/innioasis/music/MusicPlayerActivity;->getVb()Landroidx/viewbinding/ViewBinding;

    move-result-object v1

    check-cast v1, Lcom/innioasis/y1/databinding/ActivityMusicPlayerBinding;

    iget-object v1, v1, Lcom/innioasis/y1/databinding/ActivityMusicPlayerBinding;->repeatMode:Landroid/widget/ImageView;

    const/4 v2, 0x1

    if-ne v0, v2, :cond_repeat_check_2

    const v2, 0x7f0e0029

    goto :goto_repeat_set

    :cond_repeat_check_2
    const/4 v2, 0x2

    if-ne v0, v2, :cond_repeat_off

    const v2, 0x7f0e0028

    goto :goto_repeat_set

    :cond_repeat_off
    const v2, 0x7f0e0026

    :goto_repeat_set
    invoke-virtual {v1, v2}, Landroid/widget/ImageView;->setImageResource(I)V
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    return-void

    :catch_0
    move-exception v0

    return-void
.end method
"""
if "refreshRepeatShuffleUi" not in mpa_src:
    mpa_src = mpa_src.rstrip() + MPA_REFRESH_PAPP_INJECT + "\n"
with open(mpa_path, 'w') as f:
    f.write(mpa_src)
print(f"  Patch B5.5: MusicPlayerActivity onResume/onPause + refreshRepeatShuffleUi → NowPlayingRefresher")


# ============================================================
# Patch B6: AvrcpBinder smali drop (unused groundwork)
# ============================================================
#
# AvrcpBridgeService.smali + AvrcpBinder.smali land in smali_classes2/ but
# nothing instantiates them — the music APK's AndroidManifest.xml can't be
# modified to declare the service because com.innioasis.y1 sets
# sharedUserId="android.uid.system", constraining the signing key to the OEM
# platform key; any AndroidManifest.xml byte change fails JarVerifier's
# META-INF/MANIFEST.MF SHA1-Digest check at /system/app/ scan. Y1Bridge.apk
# (src/Y1Bridge/, package com.koensayr.y1.bridge, self-signed debug cert)
# hosts the Binder MtkBt actually resolves to.
#
# The smali is kept in tree so MtkBt.odex component-bind work (see
# docs/INVESTIGATION.md Trace #23) doesn't have to recreate it from scratch.

PATCH_B6_INJECT_FILES = [
    ("com/koensayr/y1/avrcp/AvrcpBridgeService.smali",
        "smali_classes2/com/koensayr/y1/avrcp/AvrcpBridgeService.smali"),
    ("com/koensayr/y1/avrcp/AvrcpBinder.smali",
        "smali_classes2/com/koensayr/y1/avrcp/AvrcpBinder.smali"),
]

for src_rel, dst_rel in PATCH_B6_INJECT_FILES:
    src = os.path.join(INJECT_ROOT, src_rel)
    dst = os.path.join(UNPACKED_DIR, dst_rel)
    if not os.path.exists(src):
        sys.exit(f"ERROR: Patch B6 source missing: {src}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    print(f"  Wrote {dst_rel}")


# -- Per-smali md5 report -----------------------------------------------------
# Hash each patched smali file. These hashes are deterministic regardless of
# Java version or apktool reassembly behavior, so they reliably indicate
# whether the smali edits succeeded.
print(f"\nPatched smali file md5s (deterministic — same across machines):")
PATCHED_SMALI_FILES = [
    ARTISTS_SMALI, ALBUMS_SMALI, REPO_SMALI,
    PLAY_CONTROLLER_RECEIVER_SMALI, BASE_ACTIVITY_SMALI,
    BASE_PLAYER_ACTIVITY_SMALI,
    Y1APP_SMALI, PAPP_RECEIVER_SMALI, PAPP_BROADCASTER_SMALI,
    # Patch B5 — in-app y1-track-info production
    STATIC_SMALI, PLAYER_SERVICE_SMALI_FOR_B5,
    "smali/com/koensayr/y1/trackinfo/TrackInfoWriter.smali",
    "smali/com/koensayr/y1/playback/PlaybackStateBridge.smali",
    "smali/com/koensayr/y1/playback/PositionTicker.smali",
    "smali/com/koensayr/y1/battery/BatteryReceiver.smali",
    "smali/com/koensayr/y1/papp/PappSetFileObserver.smali",
    "smali/com/koensayr/y1/ui/NowPlayingRefresher.smali",
    MUSIC_PLAYER_ACTIVITY_SMALI,
    # Patch B6 — AvrcpBridgeService (unused groundwork)
    "smali_classes2/com/koensayr/y1/avrcp/AvrcpBridgeService.smali",
    "smali_classes2/com/koensayr/y1/avrcp/AvrcpBinder.smali",
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

# -- Build patched APK (replace DEX, keep original META-INF + manifest) -------
# We do NOT swap AndroidManifest.xml — modifying it would invalidate
# META-INF/MANIFEST.MF's SHA1-Digest and JarVerifier rejects the package at
# /system/app/ scan with "no certificates at entry AndroidManifest.xml; ignoring!".
# Resigning would require the OEM platform key (com.innioasis.y1 declares
# sharedUserId="android.uid.system"). Modifying only DEX (leaving META-INF
# stale) works because JarVerifier only digest-checks AndroidManifest.xml at
# scan time, not DEX/resources.
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
