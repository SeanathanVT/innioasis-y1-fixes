.class public final Lcom/koensayr/y1/trackinfo/TrackInfoWriter;
.super Ljava/lang/Object;
.source "TrackInfoWriter.smali"


# Singleton holder + atomic writer for /data/data/com.innioasis.y1/files/y1-track-info.
#
# 1104-byte schema, byte offsets 0..1103. Read by the libextavrcp_jni.so trampolines
# (T1/T2/extended_T2/T4/T5/T6/T8/T9/T_papp/T_charset/T_battery) directly via
# open(2)+read(2).
#
# All public mutators are synchronized on INSTANCE. flushLocked() is called inline
# from the calling thread (Static.setPlayValue runs on main; callbacks are off-main
# but file IO is small + state-edge frequency only — single-threaded acceptable).


# static fields
.field public static final INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;


# instance fields
.field private mContext:Landroid/content/Context;

.field private mFilesDir:Ljava/io/File;

# 0=STOPPED, 1=PLAYING, 2=PAUSED — AVRCP 1.3 §5.4.1 Tbl 5.26
.field private mPlayStatus:B

.field private mPositionAtStateChange:J

.field private mStateChangeTime:J

.field private mPreviousTrackNaturalEnd:Z

# Latched between onCompletion (true) and the next onTrackEdge (consumed→cleared).
# onCompletion only fires when the player engine reaches end-of-stream, so this
# is the canonical natural-end signal — no extrapolation needed.
.field private mPendingNaturalEnd:Z

# AVRCP §5.4.2 Tbl 5.35 enum: 0=NORMAL 1=WARNING 2=CRITICAL 3=EXTERNAL 4=FULL_CHARGE
.field private mBatteryStatus:B

# AVRCP §5.2.4 Tbl 5.20 Repeat (default OFF=0x01)
.field private mRepeatAvrcp:B

# AVRCP §5.2.4 Tbl 5.21 Shuffle (default OFF=0x01)
.field private mShuffleAvrcp:B

# Cached current-track metadata populated by flushLocked, consumed by
# wakeTrackChanged / wakePlayStateChanged so MMI_AVRCP's Java mirror sees
# AOSP-convention Intent extras (id / track / artist / album / playing).
.field private mCachedAudioId:J

.field private mCachedTitle:Ljava/lang/String;

.field private mCachedArtist:Ljava/lang/String;

.field private mCachedAlbum:Ljava/lang/String;

# Last duration value PlayerService.getDuration() returned while prepared.
# flushLocked preserves it across prepare gaps so y1-track-info[776..779]
# never falls back to 0 (which CTs treat as "duration unknown" and hide
# the playhead display).
.field private mLastKnownDuration:J

# elapsedRealtime() at the most recent real (audio_id-changed) onTrackEdge
# fire. onSeek consults this to suppress the music app's
# PlayerService.playerPrepared() restore-from-saved-progress seek that
# fires after prepareAsync completes (3 setCurrentPosition sites in
# stock playerPrepared, lines 1737/1793/1923 — restoreStartTime,
# Bookmark.startTime, Progress.startTime). Those calls would otherwise
# overwrite our reset-to-0 from onEarlyTrackChange and leave Bolt/Kia's
# wire-side playhead showing the user's prior pause point on this track
# rather than 0:00 for the freshly-skipped track.
#
# Suppression window is ~2 s — covers prepareAsync (~50-500 ms) +
# OnPreparedListener dispatch + the playerPrepared restore call. User-
# initiated seeks (drag the seek bar) almost always come well after
# 2 s. Init to 0 — first track-change after boot won't trigger
# suppression, which is correct since there's no preceding fresh-track
# reset to protect.
.field private mLastFreshTrackChangeAt:J

# MediaMetadataRetriever-derived duration cache. Y1 music app stores no
# DB-cached duration; PlayerService.getDuration() delegates to
# IjkMediaPlayer/MediaPlayer.getDuration() which throws before
# prepareAsync completes. Without an alternate source the first T4
# GetElementAttributes response on every track skip carries dur=0
# (attribute 0x07 PlayingTime = "0"), which strict CTs cache as
# "duration unknown" — AVRCP 1.3 has no DURATION_CHANGED event so a
# §6.7.1-correct second TRACK_CHANGED CHANGED (same audio_id) cannot
# refresh it once the real duration arrives via B5.2c's playerPrepared
# tail ~700 ms later. MediaMetadataRetriever.setDataSource(path) +
# extractMetadata(METADATA_KEY_DURATION) reads the file's container
# header synchronously without involving the C++ MediaPlayer, so it's
# safe to call from any state. Per-audio_id cache keeps the cost to one
# header parse per track (~10-50 ms for local MP3/M4A).
.field private mMmrAudioId:J

.field private mMmrDurationMs:J


# direct methods
.method static constructor <clinit>()V
    .locals 1

    new-instance v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    invoke-direct {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;-><init>()V

    sput-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    return-void
.end method

.method private constructor <init>()V
    .locals 2

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    const/4 v0, 0x0

    iput-byte v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPlayStatus:B

    const-wide/16 v0, 0x0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mStateChangeTime:J

    const/4 v0, 0x0

    iput-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPreviousTrackNaturalEnd:Z

    iput-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPendingNaturalEnd:Z

    iput-byte v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mBatteryStatus:B

    const/4 v1, 0x1

    iput-byte v1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mRepeatAvrcp:B

    iput-byte v1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mShuffleAvrcp:B

    const-wide/16 v0, 0x0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J

    const-string v0, ""

    iput-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedTitle:Ljava/lang/String;

    iput-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedArtist:Ljava/lang/String;

    iput-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAlbum:Ljava/lang/String;

    const-wide/16 v0, 0x0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastKnownDuration:J

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mMmrAudioId:J

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mMmrDurationMs:J

    return-void
.end method


# Initialise on Application.onCreate. Idempotent.
.method public declared-synchronized init(Landroid/content/Context;)V
    .locals 3

    monitor-enter p0

    :try_start_0
    iget-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mContext:Landroid/content/Context;

    if-eqz v0, :cond_init

    monitor-exit p0

    return-void

    :cond_init
    invoke-virtual {p1}, Landroid/content/Context;->getApplicationContext()Landroid/content/Context;

    move-result-object v0

    iput-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mContext:Landroid/content/Context;

    invoke-virtual {v0}, Landroid/content/Context;->getFilesDir()Ljava/io/File;

    move-result-object v0

    iput-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mFilesDir:Ljava/io/File;

    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v1

    iput-wide v1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mStateChangeTime:J

    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->prepareFilesLocked()V

    # Flush y1-track-info immediately at init so MtkBt's first read returns
    # the in-memory defaults (Repeat=0x01 OFF, Shuffle=0x01 OFF — valid AVRCP
    # §5.2.4 Tbl 5.20 / 5.21 values) rather than the zero-fill that an
    # unwritten file would give. Without this, CTs that subscribe to
    # PLAYER_APPLICATION_SETTING_CHANGED before B4's first sendNow() can
    # latch onto file[795..796] = [0,0] (invalid AVRCP enum) and refuse to
    # follow subsequent CHANGED events.
    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->flushLocked()V
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    monitor-exit p0

    return-void

    :catchall_0
    move-exception v0

    monitor-exit p0

    throw v0
.end method


# Make filesDir traversable for the BT process (uid bluetooth) and pre-create
# the state files (y1-trampoline-state, y1-papp-set) world-rw — trampolines
# open them without O_CREAT, so they must exist before MtkBt's first probe.
.method private prepareFilesLocked()V
    .locals 4

    :try_start_0
    iget-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mFilesDir:Ljava/io/File;

    if-nez v0, :cond_dir

    return-void

    :cond_dir
    const/4 v1, 0x1

    const/4 v2, 0x0

    invoke-virtual {v0, v1, v2}, Ljava/io/File;->setExecutable(ZZ)Z

    const-string v1, "y1-trampoline-state"

    const/16 v2, 0x14

    invoke-direct {p0, v1, v2}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->ensureFile(Ljava/lang/String;I)V

    const-string v1, "y1-papp-set"

    const/4 v2, 0x2

    invoke-direct {p0, v1, v2}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->ensureFile(Ljava/lang/String;I)V
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    return-void

    :catch_0
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v0

    invoke-static {v1, v0}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


# Touch <name> to <size> bytes if missing; chmod world-rw.
.method private ensureFile(Ljava/lang/String;I)V
    .locals 4

    new-instance v0, Ljava/io/File;

    iget-object v1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mFilesDir:Ljava/io/File;

    invoke-direct {v0, v1, p1}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    invoke-virtual {v0}, Ljava/io/File;->exists()Z

    move-result v1

    if-eqz v1, :cond_exists

    return-void

    :cond_exists
    new-instance v1, Ljava/io/FileOutputStream;

    invoke-direct {v1, v0}, Ljava/io/FileOutputStream;-><init>(Ljava/io/File;)V

    :try_start_0
    new-array v2, p2, [B

    invoke-virtual {v1, v2}, Ljava/io/FileOutputStream;->write([B)V
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    invoke-virtual {v1}, Ljava/io/FileOutputStream;->close()V

    const/4 v1, 0x1

    const/4 v2, 0x0

    invoke-virtual {v0, v1, v2}, Ljava/io/File;->setReadable(ZZ)Z

    invoke-virtual {v0, v1, v2}, Ljava/io/File;->setWritable(ZZ)Z

    return-void

    :catchall_0
    move-exception v3

    invoke-virtual {v1}, Ljava/io/FileOutputStream;->close()V

    throw v3
.end method


# Public mutator: AVRCP play-status edge. Captures position/time-at-edge.
# Returns silently if status unchanged (dedupe).
#
# Inline track-edge detection (perceived-responsiveness optimisation): if
# flushLocked recomputes mCachedAudioId to a different value than the
# pre-flush snapshot, the music-app's internal nextSong/prevSong/restartPlay
# sequence has already advanced mPlayingMusic to a new track. This pause-
# flush is the earliest possible point we observe the audio_id change —
# ~260 ms BEFORE PlayerService.toRestart()'s setDataSource sites where
# B5.2b's onEarlyTrackChange currently fires. Resetting position +
# mLastKnownDuration to 0 here and re-flushing keeps the file internally
# consistent (new audio_id + new title + 0 position + 0 duration) so T4
# GetElementAttributes responses + T9 POS_CHANGED + T5 TRACK_CHANGED all
# show the CT a coherent "track just started" state in the same broadcast
# cycle. Without this, the CT briefly sees new_audio_id + new_title +
# stale_position (e.g., 15.7 s into a track that "just started"), which
# stricter CTs latch onto and visibly lag before the next consistent
# update arrives. Returns silently for resume-from-pause (audio_id
# unchanged) — no extra work.
.method public declared-synchronized setPlayStatus(B)V
    .locals 7

    monitor-enter p0

    :try_start_0
    iget-byte v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPlayStatus:B

    if-ne v0, p1, :cond_changed

    monitor-exit p0

    return-void

    :cond_changed
    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->computeLivePositionLocked()J

    move-result-wide v0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J

    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mStateChangeTime:J

    iput-byte p1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPlayStatus:B

    # Snapshot audio_id BEFORE flushLocked. The flush re-reads PlayerService
    # state which by this point may already reflect a new track (the music
    # app's nextSong/prevSong/restartPlay flow updates mPlayingMusic before
    # the pause() call that brings us here).
    iget-wide v5, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J

    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->flushLocked()V

    # Compare new mCachedAudioId (just written) with snapshot. If different,
    # this play-status edge is the leading edge of a track change — reset
    # position + duration to 0 and re-flush so the file is internally
    # consistent before any T4/T5/T6/T9 response sees it.
    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J

    cmp-long v3, v5, v0

    if-eqz v3, :cond_no_edge

    const-wide/16 v0, 0x0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastKnownDuration:J

    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastFreshTrackChangeAt:J

    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->flushLocked()V

    :cond_no_edge
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    monitor-exit p0

    return-void

    :catchall_0
    move-exception v0

    monitor-exit p0

    throw v0
.end method


# Public mutator: seek edge. Captures the new position as the live anchor
# so T6 / T9 / T8's clock_gettime extrapolation runs forward from there.
# Without this, a user-initiated seek (via the music app's seek bar) leaves
# the anchor at the previous position and the CT's playhead either jumps
# back to the pre-seek value or freezes until the next state edge.
#
# Suppression window: PlayerService.playerPrepared() in stock 3.0.2 calls
# setCurrentPosition(savedTime) at three sites (lines 1737/1793/1923 —
# restoreStartTime / Bookmark.startTime / Progress.startTime) right after
# prepareAsync completes. This is the music app's "resume from saved
# progress" feature — desirable for the local UI but it overwrites the
# reset-to-0 our onEarlyTrackChange just stamped, so Bolt/Kia would show
# the user's prior pause point on the freshly-skipped track instead of
# 0:00. We suppress onSeek for ~2 s after a fresh-track-change reset to
# defang exactly those restore calls. User-initiated seeks (drag the
# seek bar) come well after 2 s so they're unaffected.
.method public declared-synchronized onSeek(J)V
    .locals 5

    monitor-enter p0

    :try_start_0
    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastFreshTrackChangeAt:J

    const-wide/16 v2, 0x0

    cmp-long v4, v0, v2

    if-eqz v4, :cond_normal

    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v2

    sub-long/2addr v2, v0

    const-wide/16 v0, 0x7d0

    cmp-long v4, v2, v0

    if-gez v4, :cond_normal

    # Within ~2 s of a fresh track-change reset — this seek is almost
    # certainly playerPrepared's restore-from-saved-progress call.
    # Skip the position update (and the wakePlayStateChanged broadcast,
    # since nothing changed). Don't clear mLastFreshTrackChangeAt — if
    # playerPrepared somehow fires a second restore call (e.g. for
    # bookmark + progress) we want to suppress that too.
    monitor-exit p0

    return-void

    :cond_normal
    iput-wide p1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J

    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mStateChangeTime:J

    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->flushLocked()V
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_seek

    monitor-exit p0

    invoke-virtual {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->wakePlayStateChanged()V

    return-void

    :catchall_seek
    move-exception v0

    monitor-exit p0

    throw v0
.end method


# Latch a natural-end signal from MediaPlayer.OnCompletionListener. The next
# onTrackEdge consumes + clears it. Also freezes the playhead anchor at
# duration so T9 / T6 stop extrapolating past end-of-track during the gap
# until onPrepared fires for the next track — CTs hide the playhead when
# position > duration arrives on the wire.
.method public declared-synchronized markCompletion()V
    .locals 3

    monitor-enter p0

    :try_start_0
    const/4 v0, 0x1

    iput-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPendingNaturalEnd:Z

    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastKnownDuration:J

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J

    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mStateChangeTime:J

    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->flushLocked()V
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    monitor-exit p0

    return-void

    :catchall_0
    move-exception v0

    monitor-exit p0

    throw v0
.end method


# Clear any pending natural-end (e.g., on OnError — interrupted, not natural end).
.method public declared-synchronized markError()V
    .locals 1

    monitor-enter p0

    :try_start_0
    const/4 v0, 0x0

    iput-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPendingNaturalEnd:Z
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    monitor-exit p0

    return-void

    :catchall_0
    move-exception v0

    monitor-exit p0

    throw v0
.end method


# Unconditional fresh-track reset. Called from PlaybackStateBridge.onEarlyTrackChange
# (which itself is invoked from PlayerService.toRestart's setDataSource sites — a
# guaranteed track-load entry). Resets position-anchor, mLastKnownDuration, and
# stamps mLastFreshTrackChangeAt without consulting audio_id dedup.
#
# Why dedup wouldn't work here: the music-app's restartPlay() invokes pause() before
# toRestart(), and pause()'s setPlayValue → flushLocked already updates mCachedAudioId
# to the new track's id. By the time onTrackEdge would snapshot mCachedAudioId, it
# already holds the new value — so old==new and the reset branch never fires
# (verified on Kia 2026-05-14: EDGE_DETECTED count 0 across 12 skips, position
# accumulating instead of resetting).
#
# Resetting mLastKnownDuration to 0 is critical: flushLocked falls back to the cached
# duration when getPlayerIsPrepared() is false (i.e., during the prepareAsync gap),
# so without this reset the file briefly reports the previous track's duration on
# every track change. Honest 0 ("unknown duration" per AVRCP §5.3.4 / 1.3 attr 0x07)
# is preferable to a misleading-stale value. The B5.2c playerPrepared-tail hook then
# runs flush() once getPlayerIsPrepared() flips true, capturing the correct duration.
.method public declared-synchronized onFreshTrackChange()V
    .locals 3

    monitor-enter p0

    :try_start_0
    iget-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPendingNaturalEnd:Z

    iput-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPreviousTrackNaturalEnd:Z

    const/4 v0, 0x0

    iput-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPendingNaturalEnd:Z

    const-wide/16 v0, 0x0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastKnownDuration:J

    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mStateChangeTime:J

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastFreshTrackChangeAt:J

    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->flushLocked()V
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    monitor-exit p0

    return-void

    :catchall_0
    move-exception v0

    monitor-exit p0

    throw v0
.end method


# Soft track edge: dedup-gated reset for re-prepare paths that may or may not
# represent a real track change. Called from PlaybackStateBridge.onPrepared
# (OnPreparedListener fires on every prepareAsync completion, including the
# re-prepare some player engines do on pause→resume cycles of the same track).
#
# Snapshot old mCachedAudioId → flushLocked refreshes it → compare. Only resets
# position-anchor if audio_id actually changed. Real fresh-track changes are
# already handled by onFreshTrackChange via onEarlyTrackChange; this method
# exists so an OnPrepared firing for a same-track re-prepare doesn't disturb
# the existing live-position baseline.
.method public declared-synchronized onTrackEdge()V
    .locals 5

    monitor-enter p0

    :try_start_0
    # Natural-end latch (unchanged).
    iget-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPendingNaturalEnd:Z

    iput-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPreviousTrackNaturalEnd:Z

    const/4 v0, 0x0

    iput-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPendingNaturalEnd:Z

    # Snapshot the previous cached audio_id (from prior flushLocked).
    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J

    # First flush — recomputes audio_id from PlayerService.getPlayingSong()
    # and stores it in mCachedAudioId. Also refreshes title/artist/album so
    # CTs that re-query metadata immediately after the metachanged broadcast
    # see the new track even if we end up taking the same-track path below.
    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->flushLocked()V

    # Compare new audio_id (just written) with snapshot.
    iget-wide v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J

    cmp-long v4, v0, v2

    if-eqz v4, :cond_same_track

    # Real track edge — reset position anchor and re-flush.
    const-wide/16 v0, 0x0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J

    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v0

    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mStateChangeTime:J

    # Stamp the fresh-track-change time so onSeek can suppress the
    # music app's playerPrepared() restore-from-saved-progress seek
    # that fires ~50-500 ms later (after prepareAsync completes).
    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastFreshTrackChangeAt:J

    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->flushLocked()V

    :cond_same_track
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    monitor-exit p0

    return-void

    :catchall_0
    move-exception v0

    monitor-exit p0

    throw v0
.end method


# Battery bucket update; dedupe.
.method public declared-synchronized setBattery(B)V
    .locals 1

    monitor-enter p0

    :try_start_0
    iget-byte v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mBatteryStatus:B

    if-ne v0, p1, :cond_changed

    monitor-exit p0

    return-void

    :cond_changed
    iput-byte p1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mBatteryStatus:B

    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->flushLocked()V
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    monitor-exit p0

    return-void

    :catchall_0
    move-exception v0

    monitor-exit p0

    throw v0
.end method


# Public mutator: Repeat + Shuffle bytes (AVRCP §5.2.4 enum). Both at once
# because PappStateBroadcaster always sends them together.
.method public declared-synchronized setPapp(II)V
    .locals 3

    monitor-enter p0

    :try_start_0
    const/4 v0, 0x0

    int-to-byte v1, p1

    iget-byte v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mRepeatAvrcp:B

    if-eq v1, v2, :cond_no_repeat

    iput-byte v1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mRepeatAvrcp:B

    const/4 v0, 0x1

    :cond_no_repeat
    int-to-byte v1, p2

    iget-byte v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mShuffleAvrcp:B

    if-eq v1, v2, :cond_no_shuffle

    iput-byte v1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mShuffleAvrcp:B

    const/4 v0, 0x1

    :cond_no_shuffle
    if-eqz v0, :cond_done

    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->flushLocked()V

    :cond_done
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    monitor-exit p0

    return-void

    :catchall_0
    move-exception v0

    monitor-exit p0

    throw v0
.end method


# Force a flush. Used on cold-boot init and any path that wants the file
# rewritten without a state edge.
.method public declared-synchronized flush()V
    .locals 0

    monitor-enter p0

    :try_start_0
    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->flushLocked()V
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    monitor-exit p0

    return-void

    :catchall_0
    move-exception v0

    monitor-exit p0

    throw v0
.end method


# Live position with playing-state extrapolation, capped at duration.
# Caller must hold monitor.
.method private computeLivePositionLocked()J
    .locals 7

    iget-byte v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPlayStatus:B

    const/4 v1, 0x1

    if-eq v0, v1, :cond_playing

    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J

    return-wide v0

    :cond_playing
    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v0

    iget-wide v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mStateChangeTime:J

    sub-long/2addr v0, v2

    iget-wide v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J

    add-long/2addr v0, v2

    invoke-static {}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->getPlayerService()Lcom/innioasis/y1/service/PlayerService;

    move-result-object v2

    if-eqz v2, :cond_done

    # Same MediaPlayer-getDuration-during-prepareAsync hazard as flushLocked:
    # gate on getPlayerIsPrepared so we don't trip the C++ player into Error
    # state when extrapolating position around a track edge. Skip the cap
    # if not prepared (live position is just elapsed-since-state-change anyway).
    invoke-virtual {v2}, Lcom/innioasis/y1/service/PlayerService;->getPlayerIsPrepared()Z

    move-result v4

    if-eqz v4, :cond_done

    invoke-virtual {v2}, Lcom/innioasis/y1/service/PlayerService;->getDuration()J

    move-result-wide v2

    const-wide/16 v4, 0x0

    cmp-long v6, v2, v4

    if-lez v6, :cond_done

    cmp-long v6, v0, v2

    if-lez v6, :cond_done

    move-wide v0, v2

    :cond_done
    return-wide v0
.end method


.method static getPlayerService()Lcom/innioasis/y1/service/PlayerService;
    .locals 1

    sget-object v0, Lcom/innioasis/y1/Y1Application;->Companion:Lcom/innioasis/y1/Y1Application$Companion;

    invoke-virtual {v0}, Lcom/innioasis/y1/Y1Application$Companion;->getPlayerService()Lcom/innioasis/y1/service/PlayerService;

    move-result-object v0

    return-object v0
.end method


# The actual file writer. Caller must hold monitor.
# 1104-byte buffer; atomic tmp+rename; world-readable on creation.
.method private flushLocked()V
    .locals 14

    :try_start_top
    iget-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mFilesDir:Ljava/io/File;

    if-nez v0, :cond_have_dir

    return-void

    :cond_have_dir
    const/16 v1, 0x450

    new-array v1, v1, [B

    # Read live state from PlayerService. v2 = svc, v3 = song, v4-v9 = strings + audioId
    invoke-static {}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->getPlayerService()Lcom/innioasis/y1/service/PlayerService;

    move-result-object v2

    const/4 v3, 0x0

    const-string v4, ""

    move-object v5, v4

    move-object v6, v4

    move-object v7, v4

    const/4 v8, 0x0

    const-wide/16 v9, 0x0

    move-wide v11, v9

    const/4 v13, 0x0

    if-eqz v2, :cond_no_svc

    invoke-virtual {v2}, Lcom/innioasis/y1/service/PlayerService;->getPlayingSong()Lcom/innioasis/y1/database/Song;

    move-result-object v3

    if-nez v3, :cond_have_song

    invoke-virtual {v2}, Lcom/innioasis/y1/service/PlayerService;->getPlayingMusic()Lcom/innioasis/y1/database/Song;

    move-result-object v3

    :cond_have_song
    if-eqz v3, :cond_no_song

    invoke-virtual {v3}, Lcom/innioasis/y1/database/Song;->getSongName()Ljava/lang/String;

    move-result-object v4

    invoke-static {v4}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->safeStr(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v4

    invoke-virtual {v3}, Lcom/innioasis/y1/database/Song;->getArtist()Ljava/lang/String;

    move-result-object v5

    invoke-static {v5}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->safeStr(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v5

    invoke-virtual {v3}, Lcom/innioasis/y1/database/Song;->getAlbum()Ljava/lang/String;

    move-result-object v6

    invoke-static {v6}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->safeStr(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v6

    invoke-virtual {v3}, Lcom/innioasis/y1/database/Song;->getGenre()Ljava/lang/String;

    move-result-object v7

    invoke-static {v7}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->safeStr(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v7

    invoke-virtual {v3}, Lcom/innioasis/y1/database/Song;->getPath()Ljava/lang/String;

    move-result-object v8

    invoke-static {v8}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->syntheticAudioId(Ljava/lang/String;)J

    move-result-wide v9

    :cond_no_song
    # PlayerService.getDuration() delegates to MediaPlayer.getDuration() for non-IJK
    # paths, which crashes the C++ MediaPlayer ("Attempt to call getDuration without
    # a valid mediaplayer" → INVALID_OPERATION → async OnError -38) when called
    # between setDataSource and OnPrepared. The music app calls Static.setPlayValue
    # inside its restart sequence BEFORE prepareAsync completes, so flushing here
    # without a guard would nuke the new MediaPlayer mid-prepare and leave the UI
    # stuck at 0:00. Gate on getPlayerIsPrepared (a pure iget-boolean, safe in any
    # state); when not prepared, fall back to MediaMetadataRetriever (cached per
    # audio_id) so the first T4 response for a new track carries the real duration
    # rather than 0. AVRCP 1.3 has no DURATION_CHANGED event — a CT that caches
    # dur=0 from the first T4 will keep it until the next track change.
    invoke-virtual {v2}, Lcom/innioasis/y1/service/PlayerService;->getPlayerIsPrepared()Z

    move-result v0

    if-eqz v0, :cond_skip_duration

    invoke-virtual {v2}, Lcom/innioasis/y1/service/PlayerService;->getDuration()J

    move-result-wide v11

    # Cache the live duration so we can fall back to it when getPlayerIsPrepared
    # goes false during a prepare gap. Without this, the duration field in
    # y1-track-info briefly resets to 0 and the CT loses its playhead display
    # ("0:00 / 0:00" or hidden entirely) until the next prepare completes.
    iput-wide v11, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastKnownDuration:J

    goto :cond_have_duration

    :cond_skip_duration
    # Pre-prepare path. Try MMR cache first (per-audio_id parse of file
    # container header — no MediaPlayer involvement). v8 = path string,
    # v9:v10 = synthetic audio_id long. Result lands in v11:v12 (long).
    #
    # Register-type discipline at :cond_have_duration: the "true" branch
    # above wrote v0 as int (boolean from getPlayerIsPrepared move-result)
    # and we reach the merge via goto. Dalvik 4.x's verifier joins
    # register types at the merge — if THIS branch writes v0 as long
    # (e.g., const-wide/16 v0, 0x0 for a cmp-long), the join becomes
    # int|long-low → conflict, and the class fails verification with
    # VerifyError at Y1Application.onCreate even though v0 is later
    # overwritten (Dalvik 4.x is strict about conflict-state merges).
    #
    # Use long-to-int instead to derive a sign-test int without touching
    # v0 as long. AVRCP duration is u32 in the file schema (max ~4.3 B ms
    # = ~50 days, well beyond any real track), so v11 alone (the low half
    # of the long pair) is a safe int representation for the sign check.
    invoke-direct {p0, v8, v9, v10}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->getMmrDurationLocked(Ljava/lang/String;J)J

    move-result-wide v11

    long-to-int v0, v11

    if-gtz v0, :cond_have_duration

    # MMR returned 0 (failure or unsupported codec) — last-resort fallback
    # to the legacy cached duration. mLastKnownDuration is reset to 0 by
    # setPlayStatus's inline edge detection on track changes, so this
    # typically yields 0 for fresh tracks where MMR also failed; the wire
    # result is dur=0, same as pre-MMR behaviour.
    iget-wide v11, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastKnownDuration:J

    :cond_have_duration
    invoke-virtual {v2}, Lcom/innioasis/y1/service/PlayerService;->getMusicIndex()I

    move-result v13

    add-int/lit8 v13, v13, 0x1

    :cond_no_svc
    # Cache the live metadata so wakeTrackChanged / wakePlayStateChanged can
    # emit AOSP-convention Intent extras without re-reading PlayerService
    # (which can return null mid-prepare).
    iput-wide v9, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J

    iput-object v4, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedTitle:Ljava/lang/String;

    iput-object v5, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedArtist:Ljava/lang/String;

    iput-object v6, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAlbum:Ljava/lang/String;

    # bytes 0..7 = audio_id (BE u64)
    const/4 v0, 0x0

    invoke-static {v1, v0, v9, v10}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putBE64([BIJ)V

    # Strings: title @ 8 (256), artist @ 264 (256), album @ 520 (256)
    const/16 v0, 0x8

    const/16 v2, 0x100

    invoke-static {v1, v0, v2, v4}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putUtf8Padded([BIILjava/lang/String;)V

    const/16 v0, 0x108

    invoke-static {v1, v0, v2, v5}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putUtf8Padded([BIILjava/lang/String;)V

    const/16 v0, 0x208

    invoke-static {v1, v0, v2, v6}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putUtf8Padded([BIILjava/lang/String;)V

    # duration_ms BE @ 776; clamp negatives to 0
    const-wide/16 v2, 0x0

    cmp-long v0, v11, v2

    if-gtz v0, :cond_pos_dur

    move-wide v11, v2

    :cond_pos_dur
    const/16 v0, 0x308

    long-to-int v2, v11

    invoke-static {v1, v0, v2}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putBE32([BII)V

    # pos_at_state_change BE @ 780
    const/16 v0, 0x30c

    iget-wide v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J

    long-to-int v2, v2

    invoke-static {v1, v0, v2}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putBE32([BII)V

    # state_change_time BE @ 784 (low 32 bits of elapsedRealtime)
    const/16 v0, 0x310

    iget-wide v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mStateChangeTime:J

    long-to-int v2, v2

    invoke-static {v1, v0, v2}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putBE32([BII)V

    # bytes 788..791 pad

    # play_status @ 792
    const/16 v0, 0x318

    iget-byte v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPlayStatus:B

    aput-byte v2, v1, v0

    # natural_end @ 793
    const/16 v0, 0x319

    iget-boolean v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPreviousTrackNaturalEnd:Z

    if-eqz v2, :cond_ne_zero

    const/4 v2, 0x1

    goto :goto_ne_done

    :cond_ne_zero
    const/4 v2, 0x0

    :goto_ne_done
    int-to-byte v2, v2

    aput-byte v2, v1, v0

    # battery @ 794
    const/16 v0, 0x31a

    iget-byte v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mBatteryStatus:B

    aput-byte v2, v1, v0

    # repeat @ 795, shuffle @ 796
    const/16 v0, 0x31b

    iget-byte v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mRepeatAvrcp:B

    aput-byte v2, v1, v0

    const/16 v0, 0x31c

    iget-byte v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mShuffleAvrcp:B

    aput-byte v2, v1, v0

    # GetElementAttributes attrs 4-7 — pre-formatted ASCII decimal slots.
    # TrackNumber @ 800 (16), TotalNumberOfTracks @ 816 (16), PlayingTime @ 832 (16), Genre @ 848 (256)
    const/16 v0, 0x320

    const/16 v2, 0x10

    invoke-static {v13}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->intToDecOrEmpty(I)Ljava/lang/String;

    move-result-object v3

    invoke-static {v1, v0, v2, v3}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putUtf8Padded([BIILjava/lang/String;)V

    # totalTracks: read getMusicList().size() if svc available
    invoke-static {}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->getPlayerService()Lcom/innioasis/y1/service/PlayerService;

    move-result-object v3

    const/4 v4, 0x0

    if-eqz v3, :cond_no_total

    invoke-virtual {v3}, Lcom/innioasis/y1/service/PlayerService;->getMusicList()Ljava/util/List;

    move-result-object v3

    if-eqz v3, :cond_no_total

    invoke-interface {v3}, Ljava/util/List;->size()I

    move-result v4

    :cond_no_total
    const/16 v0, 0x330

    invoke-static {v4}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->intToDecOrEmpty(I)Ljava/lang/String;

    move-result-object v3

    invoke-static {v1, v0, v2, v3}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putUtf8Padded([BIILjava/lang/String;)V

    const/16 v0, 0x340

    invoke-static {v11, v12}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->longToDecOrEmpty(J)Ljava/lang/String;

    move-result-object v3

    invoke-static {v1, v0, v2, v3}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putUtf8Padded([BIILjava/lang/String;)V

    const/16 v0, 0x350

    const/16 v2, 0x100

    invoke-static {v1, v0, v2, v7}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->putUtf8Padded([BIILjava/lang/String;)V

    # Atomic write to filesDir/y1-track-info.tmp -> rename to y1-track-info
    new-instance v0, Ljava/io/File;

    iget-object v2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mFilesDir:Ljava/io/File;

    const-string v3, "y1-track-info.tmp"

    invoke-direct {v0, v2, v3}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    new-instance v2, Ljava/io/File;

    iget-object v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mFilesDir:Ljava/io/File;

    const-string v4, "y1-track-info"

    invoke-direct {v2, v3, v4}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    new-instance v3, Ljava/io/FileOutputStream;

    invoke-direct {v3, v0}, Ljava/io/FileOutputStream;-><init>(Ljava/io/File;)V

    :try_start_inner
    invoke-virtual {v3, v1}, Ljava/io/FileOutputStream;->write([B)V
    :try_end_inner
    .catchall {:try_start_inner .. :try_end_inner} :catchall_inner

    invoke-virtual {v3}, Ljava/io/FileOutputStream;->close()V

    invoke-virtual {v0, v2}, Ljava/io/File;->renameTo(Ljava/io/File;)Z

    move-result v3

    if-nez v3, :cond_renamed

    invoke-virtual {v0}, Ljava/io/File;->delete()Z

    return-void

    :cond_renamed
    const/4 v0, 0x1

    const/4 v3, 0x0

    invoke-virtual {v2, v0, v3}, Ljava/io/File;->setReadable(ZZ)Z
    :try_end_top
    .catch Ljava/lang/Throwable; {:try_start_top .. :try_end_top} :catch_top

    return-void

    :catchall_inner
    move-exception v4

    invoke-virtual {v3}, Ljava/io/FileOutputStream;->close()V

    throw v4

    :catch_top
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v0

    invoke-static {v1, v0}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


# Helpers

# MediaMetadataRetriever-backed duration getter. Per-audio_id cache: only
# the first call for a given audio_id parses the file container; subsequent
# calls return the cached value in microseconds. Failures (unreadable file,
# unsupported codec, malformed metadata) latch a cached 0 for that audio_id
# so we don't retry on every flush.
#
# Caller must hold the TrackInfoWriter monitor.
.method private getMmrDurationLocked(Ljava/lang/String;J)J
    .locals 7

    # Cache check: if cached audio_id matches current, return cached duration
    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mMmrAudioId:J

    cmp-long v6, v0, p2

    if-nez v6, :cond_cache_miss

    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mMmrDurationMs:J

    # Re-mirror into mLastKnownDuration. setPlayStatus's inline-edge reset
    # zeroes mLastKnownDuration between the two flushLocked calls; without
    # this, the second flush would write the cached MMR value via v11:v12
    # but leave mLastKnownDuration stale at 0 (visible in --debug fL.dur).
    iput-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastKnownDuration:J

    return-wide v0

    :cond_cache_miss
    # Latch the audio_id immediately so a failed parse caches 0 and avoids
    # re-attempting on every subsequent flush during this track.
    iput-wide p2, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mMmrAudioId:J

    const-wide/16 v3, 0x0

    iput-wide v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mMmrDurationMs:J

    # Null path → return 0
    if-eqz p1, :cond_return

    # Construct the MediaMetadataRetriever OUTSIDE the try block. Dalvik
    # 4.x's verifier rejects code where a catch handler is reachable while
    # any register holds an uninitialized reference — `new-instance` produces
    # an uninit ref and `invoke-direct <init>` only marks it initialized on
    # successful return. If either of those instructions were inside the try
    # range, the catch handler entry would observe v0 as "uninit MMR", which
    # is a verify-time error (the stock `com/innioasis/music/util/Other`'s
    # `getAlbumCover` uses the same out-of-try construction pattern).
    new-instance v0, Landroid/media/MediaMetadataRetriever;

    invoke-direct {v0}, Landroid/media/MediaMetadataRetriever;-><init>()V

    :try_start_mmr
    invoke-virtual {v0, p1}, Landroid/media/MediaMetadataRetriever;->setDataSource(Ljava/lang/String;)V

    # METADATA_KEY_DURATION = 9 (android.media.MediaMetadataRetriever)
    const/16 v1, 0x9

    invoke-virtual {v0, v1}, Landroid/media/MediaMetadataRetriever;->extractMetadata(I)Ljava/lang/String;

    move-result-object v2

    invoke-virtual {v0}, Landroid/media/MediaMetadataRetriever;->release()V

    if-eqz v2, :cond_return

    invoke-static {v2}, Ljava/lang/Long;->parseLong(Ljava/lang/String;)J

    move-result-wide v5

    # cmp result into v1 (kept int across the try), not v0 (kept MMR object
    # across the try). The verifier joins register types at catch entry over
    # every throwing instruction in the try region; writing v0 as int late
    # in the try would make catch-entry v0 a conflict (MMR vs int), which
    # Dalvik 4.x rejects even though move-exception immediately overwrites.
    cmp-long v1, v5, v3

    if-lez v1, :cond_return

    iput-wide v5, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mMmrDurationMs:J

    # Mirror into mLastKnownDuration so the legacy fallback path + the
    # --debug fL.dur log read the same coherent value.
    iput-wide v5, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mLastKnownDuration:J
    :try_end_mmr
    .catch Ljava/lang/Throwable; {:try_start_mmr .. :try_end_mmr} :catch_mmr

    :cond_return
    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mMmrDurationMs:J

    return-wide v0

    :catch_mmr
    move-exception v0

    iget-wide v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mMmrDurationMs:J

    return-wide v0
.end method


.method private static safeStr(Ljava/lang/String;)Ljava/lang/String;
    .locals 1

    if-nez p0, :cond_nn

    const-string v0, ""

    return-object v0

    :cond_nn
    return-object p0
.end method


# Stable u64 from path: ((path.hashCode() & 0xFFFFFFFFL) | 0x100000000L). High
# bit distinguishes the synthetic id from MediaStore _ID values (which are u32).
.method private static syntheticAudioId(Ljava/lang/String;)J
    .locals 4

    if-nez p0, :cond_nn

    const-wide v0, 0x100000000L

    return-wide v0

    :cond_nn
    invoke-virtual {p0}, Ljava/lang/String;->hashCode()I

    move-result v0

    int-to-long v0, v0

    const-wide v2, 0xffffffffL

    and-long/2addr v0, v2

    const-wide v2, 0x100000000L

    or-long/2addr v0, v2

    return-wide v0
.end method


.method private static intToDecOrEmpty(I)Ljava/lang/String;
    .locals 1

    if-gtz p0, :cond_pos

    const-string v0, ""

    return-object v0

    :cond_pos
    invoke-static {p0}, Ljava/lang/Integer;->toString(I)Ljava/lang/String;

    move-result-object v0

    return-object v0
.end method


.method private static longToDecOrEmpty(J)Ljava/lang/String;
    .locals 3

    const-wide/16 v0, 0x0

    cmp-long v2, p0, v0

    if-gtz v2, :cond_pos

    const-string v0, ""

    return-object v0

    :cond_pos
    invoke-static {p0, p1}, Ljava/lang/Long;->toString(J)Ljava/lang/String;

    move-result-object v0

    return-object v0
.end method


.method private static putBE64([BIJ)V
    .locals 6

    const/4 v0, 0x0

    :goto_loop
    const/16 v1, 0x8

    if-ge v0, v1, :cond_done

    rsub-int/lit8 v1, v0, 0x7

    shl-int/lit8 v1, v1, 0x3

    shr-long v2, p2, v1

    long-to-int v2, v2

    and-int/lit16 v2, v2, 0xff

    int-to-byte v2, v2

    add-int v3, p1, v0

    aput-byte v2, p0, v3

    add-int/lit8 v0, v0, 0x1

    goto :goto_loop

    :cond_done
    return-void
.end method


.method private static putBE32([BII)V
    .locals 2

    shr-int/lit8 v0, p2, 0x18

    int-to-byte v0, v0

    aput-byte v0, p0, p1

    add-int/lit8 v1, p1, 0x1

    shr-int/lit8 v0, p2, 0x10

    int-to-byte v0, v0

    aput-byte v0, p0, v1

    add-int/lit8 v1, p1, 0x2

    shr-int/lit8 v0, p2, 0x8

    int-to-byte v0, v0

    aput-byte v0, p0, v1

    add-int/lit8 v1, p1, 0x3

    int-to-byte v0, p2

    aput-byte v0, p0, v1

    return-void
.end method


# UTF-8 codepoint-safe truncation; cap = min(slot-1, 240). Trailing NUL implicit
# (caller passes a zero-initialised buffer).
.method private static putUtf8Padded([BIILjava/lang/String;)V
    .locals 7

    if-nez p3, :cond_have

    return-void

    :cond_have
    :try_start_0
    const-string v0, "UTF-8"

    invoke-virtual {p3, v0}, Ljava/lang/String;->getBytes(Ljava/lang/String;)[B

    move-result-object v0
    :try_end_0
    .catch Ljava/io/UnsupportedEncodingException; {:try_start_0 .. :try_end_0} :catch_0

    add-int/lit8 v1, p2, -0x1

    const/16 v2, 0xf0

    if-le v1, v2, :cond_cap_ok

    move v1, v2

    :cond_cap_ok
    array-length v2, v0

    if-ge v2, v1, :cond_use_cap

    move v3, v2

    goto :goto_have_n

    :cond_use_cap
    move v3, v1

    :goto_have_n
    # codepoint-safe truncation: walk back if v3 lands on a 0x80..0xBF continuation byte
    :goto_walk
    if-lez v3, :cond_walk_done

    if-ge v3, v2, :cond_walk_done

    aget-byte v4, v0, v3

    and-int/lit16 v5, v4, 0xc0

    const/16 v6, 0x80

    if-ne v5, v6, :cond_walk_done

    add-int/lit8 v3, v3, -0x1

    goto :goto_walk

    :cond_walk_done
    const/4 v4, 0x0

    invoke-static {v0, v4, p0, p1, v3}, Ljava/lang/System;->arraycopy(Ljava/lang/Object;ILjava/lang/Object;II)V

    return-void

    :catch_0
    move-exception v0

    return-void
.end method


# Wake the trampoline chain's track-changed dispatch by firing
# com.android.music.metachanged. MtkBt.odex's cardinality-NOP-patched
# BTAvrcpMusicAdapter.handleKeyMessage sswitch_1a3 wakes
# notificationTrackChangedNative on this broadcast, which jumps to T5 →
# AVRCP §5.4.2 Tbl 5.30 TRACK_CHANGED CHANGED (+ §5.4.2 Tbl 5.31/5.32 if
# the natural-end / start-of-track edges are armed).
#
# Call site: PlaybackStateBridge.onPrepared, after onTrackEdge has flushed
# the new track's y1-track-info to disk.
.method public wakeTrackChanged()V
    .locals 5

    :try_start_0
    iget-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mContext:Landroid/content/Context;

    if-eqz v0, :cond_no_ctx

    new-instance v1, Landroid/content/Intent;

    const-string v2, "com.android.music.metachanged"

    invoke-direct {v1, v2}, Landroid/content/Intent;-><init>(Ljava/lang/String;)V

    # AOSP-convention Intent extras: id (long), track (String), artist (String),
    # album (String). MMI_AVRCP's onReceive reads these directly into its Java
    # mirror; without them MtkBt logs `track-info id:-1` and gates downstream
    # notification dispatch on stale defaults.
    const-string v2, "id"

    iget-wide v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J

    invoke-virtual {v1, v2, v3, v4}, Landroid/content/Intent;->putExtra(Ljava/lang/String;J)Landroid/content/Intent;

    const-string v2, "track"

    iget-object v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedTitle:Ljava/lang/String;

    invoke-virtual {v1, v2, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;

    const-string v2, "artist"

    iget-object v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedArtist:Ljava/lang/String;

    invoke-virtual {v1, v2, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;

    const-string v2, "album"

    iget-object v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAlbum:Ljava/lang/String;

    invoke-virtual {v1, v2, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;

    invoke-virtual {v0, v1}, Landroid/content/Context;->sendBroadcast(Landroid/content/Intent;)V

    :cond_no_ctx
    return-void
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    :catch_0
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v2

    invoke-static {v1, v2}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


# Wake the trampoline chain's play-status / battery / position / papp
# dispatch by firing com.android.music.playstatechanged. MtkBt.odex's
# cardinality-NOP-patched BTAvrcpMusicAdapter.handleKeyMessage sswitch_18a
# wakes notificationPlayStatusChangedNative on this broadcast, which jumps
# to T9 → AVRCP §5.4.2 CHANGED for the four events T9 handles (PLAYBACK_STATUS
# 0x01, PLAYBACK_POS 0x05, BATT_STATUS 0x06, PLAYER_APPLICATION_SETTING 0x08;
# each gated on its own file vs state edge inside T9).
#
# Call sites: PlaybackStateBridge.onPlayValue (state-edge wake), and
# PlaybackStateBridge.onPrepared (new-track wake — position resets to 0).
.method public wakePlayStateChanged()V
    .locals 6

    :try_start_0
    iget-object v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mContext:Landroid/content/Context;

    if-eqz v0, :cond_no_ctx

    new-instance v1, Landroid/content/Intent;

    const-string v2, "com.android.music.playstatechanged"

    invoke-direct {v1, v2}, Landroid/content/Intent;-><init>(Ljava/lang/String;)V

    # AOSP-convention Intent extras: id (long), track / artist / album
    # (String), playing (boolean), and position (long, ms since track
    # start). MMI_AVRCP's onReceive logs `update-info playing:<bool>` +
    # `track-info isPlaying:<bool> id:<long>` from these extras directly.
    # Without "position", MtkBt's Java mirror's position cache defaults
    # to 0 and the AVRCP response path may inject 0 instead of using the
    # trampoline-computed live value from y1-track-info — Kia symptom
    # (2026-05-15): correct title/artist/album/duration but stuck
    # playhead. Compute the live position via the same arithmetic T9/T6
    # use (saved_pos + elapsed-since-state-change when playing).
    const-string v2, "id"

    iget-wide v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAudioId:J

    invoke-virtual {v1, v2, v3, v4}, Landroid/content/Intent;->putExtra(Ljava/lang/String;J)Landroid/content/Intent;

    const-string v2, "track"

    iget-object v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedTitle:Ljava/lang/String;

    invoke-virtual {v1, v2, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;

    const-string v2, "artist"

    iget-object v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedArtist:Ljava/lang/String;

    invoke-virtual {v1, v2, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;

    const-string v2, "album"

    iget-object v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mCachedAlbum:Ljava/lang/String;

    invoke-virtual {v1, v2, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;

    # position (long ms) — live-extrapolated via computeLivePositionLocked.
    # Caller holds the monitor; computeLivePositionLocked is monitor-safe.
    const-string v2, "position"

    invoke-direct {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->computeLivePositionLocked()J

    move-result-wide v3

    invoke-virtual {v1, v2, v3, v4}, Landroid/content/Intent;->putExtra(Ljava/lang/String;J)Landroid/content/Intent;

    const-string v2, "playing"

    iget-byte v3, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPlayStatus:B

    const/4 v5, 0x1

    if-eq v3, v5, :cond_playing

    const/4 v5, 0x0

    :cond_playing
    invoke-virtual {v1, v2, v5}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Z)Landroid/content/Intent;

    invoke-virtual {v0, v1}, Landroid/content/Context;->sendBroadcast(Landroid/content/Intent;)V

    :cond_no_ctx
    return-void
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    :catch_0
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v2

    invoke-static {v1, v2}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method
