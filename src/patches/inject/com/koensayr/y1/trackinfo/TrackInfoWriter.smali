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

    const/16 v2, 0x10

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
.method public declared-synchronized setPlayStatus(B)V
    .locals 5

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


# Latch a natural-end signal from MediaPlayer.OnCompletionListener. The next
# onTrackEdge consumes + clears it.
.method public declared-synchronized markCompletion()V
    .locals 1

    monitor-enter p0

    :try_start_0
    const/4 v0, 0x1

    iput-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPendingNaturalEnd:Z

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


# Track edge: consume the pending natural-end latch, reset position+time, flush.
# Called from OnPreparedListener (track is now decoded and playable).
.method public declared-synchronized onTrackEdge()V
    .locals 3

    monitor-enter p0

    :try_start_0
    iget-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPendingNaturalEnd:Z

    iput-boolean v0, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPreviousTrackNaturalEnd:Z

    const/4 v1, 0x0

    iput-boolean v1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPendingNaturalEnd:Z

    const-wide/16 v1, 0x0

    iput-wide v1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mPositionAtStateChange:J

    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v1

    iput-wide v1, p0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->mStateChangeTime:J

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
    # state) and write 0 for unknown duration.
    invoke-virtual {v2}, Lcom/innioasis/y1/service/PlayerService;->getPlayerIsPrepared()Z

    move-result v0

    if-eqz v0, :cond_skip_duration

    invoke-virtual {v2}, Lcom/innioasis/y1/service/PlayerService;->getDuration()J

    move-result-wide v11

    :cond_skip_duration
    invoke-virtual {v2}, Lcom/innioasis/y1/service/PlayerService;->getMusicIndex()I

    move-result v13

    add-int/lit8 v13, v13, 0x1

    :cond_no_svc
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
