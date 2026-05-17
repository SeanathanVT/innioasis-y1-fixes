.class public final Lcom/koensayr/y1/playback/PlaybackStateBridge;
.super Ljava/lang/Object;
.source "PlaybackStateBridge.smali"


# Stateless dispatcher: music-app callbacks → TrackInfoWriter mutations.
# Hooked at:
#   - Static.setPlayValue(II)V (one prepend per method body — canonical state-edge entry)
#   - PlayerService initPlayer / initPlayer2 listener lambdas (six prepends)
#   - PlayerService restartPlay / autoSwitch / nextSong / prevSong (markTrackChange prepend)
#
# Every public static method is wrapped in try/catch(Throwable) so a bug or
# unexpected state in this code path can NEVER propagate into the host method.
# The hooks are observation-only by contract: stock playback semantics must
# remain identical regardless of what we do in here. A swallowed exception
# logs a single Log.w line ("Y1Patch") and the host lambda continues.


# trackChangeDeadlineMs: SystemClock.elapsedRealtime() value (in ms) before
# which onPlayValue should SUPPRESS PLAYBACK_STATUS_CHANGED CHANGED emits for
# newValue=3 (PAUSED). PlayerService's track-change paths (restartPlay /
# autoSwitch / nextSong / prevSong) call markTrackChange() at entry; the call
# sets the deadline to elapsedRealtime() + 1000ms. The pause-then-play
# transient inside restartPlay (IjkMediaPlayer.reset + setDataSource +
# prepareAsync) emits setPlayValue(3, 3) followed within ~300ms by
# setPlayValue(1, 8); suppressing the PLAYBACK_STATUS_CHANGED wake during
# this window prevents a transient pstat=PAUSED CHANGED from reaching the CT
# (which would otherwise trip CT-side rapid-state-change back-off heuristics
# observed on subscription-class CTs). Wire-side effect: CT sees TRACK_CHANGED
# CHANGED for the new track + the post-resume pstat=PLAYING CHANGED, no
# spurious paused-in-between flap. User-initiated pause (cond_pause_strict)
# is unaffected unless it happens to fire within 1s of a track-change entry,
# which is a rare edge case.
.field private static trackChangeDeadlineMs:J


# direct methods
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


# Mark a track-change in progress. Called from PlayerService.restartPlay(Z) /
# autoSwitch() / nextSong() / prevSong() entry prepends. Sets the suppression
# deadline 1s into the future on the monotonic SystemClock.elapsedRealtime
# clock.
.method public static markTrackChange()V
    .locals 5

    :try_start_mtc
    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v0

    const-wide/16 v2, 0x3e8

    add-long/2addr v0, v2

    sput-wide v0, Lcom/koensayr/y1/playback/PlaybackStateBridge;->trackChangeDeadlineMs:J

    return-void
    :try_end_mtc
    .catch Ljava/lang/Throwable; {:try_start_mtc .. :try_end_mtc} :catch_mtc

    :catch_mtc
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v2

    invoke-static {v1, v2}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


# Static.setPlayValue(int newValue, int reason) hook. Maps newValue → AVRCP
# play_status byte (AVRCP 1.3 §5.4.1 Tbl 5.26):
#   newValue 0 → STOPPED (0x00)
#   newValue 1 → PLAYING (0x01)
#   newValue 3 → PAUSED  (0x02)
#   newValue 5 → STOPPED (0x00)
# Other values (2/4/6/7/8/9 — internal Y1 transitions) are ignored.
.method public static onPlayValue(II)V
    .locals 8

    :try_start_b5
    const/4 v0, -0x1

    if-nez p0, :cond_one

    const/4 v0, 0x0

    goto :goto_dispatch

    :cond_one
    const/4 v1, 0x1

    if-ne p0, v1, :cond_three

    const/4 v0, 0x1

    goto :goto_dispatch

    :cond_three
    const/4 v1, 0x3

    if-ne p0, v1, :cond_five

    const/4 v0, 0x2

    goto :goto_dispatch

    :cond_five
    const/4 v1, 0x5

    if-ne p0, v1, :cond_unmapped

    const/4 v0, 0x0

    :goto_dispatch
    if-ltz v0, :cond_unmapped

    sget-object v1, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    int-to-byte v0, v0

    invoke-virtual {v1, v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->setPlayStatus(B)V

    # Track-change blip suppression. If v0 == 2 (AVRCP PAUSED) AND
    # elapsedRealtime() < trackChangeDeadlineMs, skip the wakePlayStateChanged
    # so the CT doesn't see a transient pstat=PAUSED CHANGED during the
    # restartPlay pause→play handshake. setPlayStatus has already flushed the
    # file synchronously, so polling CTs (T6 GetPlayStatus) still see correct
    # state during the gap. The wakeTrackChanged below is unaffected — T5
    # still fires, the CT still gets TRACK_CHANGED for the new track.
    const/4 v3, 0x2

    if-ne v0, v3, :do_wake_play_state

    invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J

    move-result-wide v3

    sget-wide v5, Lcom/koensayr/y1/playback/PlaybackStateBridge;->trackChangeDeadlineMs:J

    cmp-long v7, v3, v5

    if-ltz v7, :skip_wake_play_state

    :do_wake_play_state
    # State-edge wake: setPlayStatus has flushed y1-track-info[792]/[780..787]
    # synchronously; fire playstatechanged so MtkBt routes through T9 and emits
    # PLAYBACK_STATUS / POS CHANGED. Also fire metachanged so MtkBt's Java
    # mirror picks up the latest AOSP-convention extras (id/track/artist/album);
    # setPlayStatus may have just detected a fresh-track edge (audio_id changed
    # since prior flush — the pause-flush leading edge of a nextSong/prevSong
    # sequence) and reset position+duration to 0. The metachanged wake ensures
    # T5 sees the new audio_id mirror-vs-file mismatch in the same broadcast
    # cycle as T9's POS reset emit, so the CT gets a coherent "track just
    # started" update ~260 ms earlier than B5.2b's toRestart-setDataSource
    # hook would. wakeTrackChanged is idempotent when no edge is present
    # (T5 dedups via file[0..7] vs state[0..7]) — safe to fire on every
    # play-status edge.
    invoke-virtual {v1}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->wakePlayStateChanged()V

    :skip_wake_play_state
    invoke-virtual {v1}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->wakeTrackChanged()V

    # Drive the 1 s position-tick loop. AVRCP 1.3 §5.4.2 Tbl 5.33 leaves the
    # PLAYBACK_POS_CHANGED cadence to the TG; T9 has the live-extrapolated
    # position via clock_gettime, but a 1.3 CT that anchors playhead rendering
    # on CHANGED events (rather than polling GetPlayStatus) needs us to fire
    # at a steady cadence while playing. Start on the PLAYING edge, stop on
    # PAUSED / STOPPED.
    const/4 v2, 0x1

    if-ne v0, v2, :cond_not_playing

    invoke-static {}, Lcom/koensayr/y1/playback/PositionTicker;->start()V

    goto :cond_unmapped

    :cond_not_playing
    invoke-static {}, Lcom/koensayr/y1/playback/PositionTicker;->stop()V

    :cond_unmapped
    return-void
    :try_end_b5
    .catch Ljava/lang/Throwable; {:try_start_b5 .. :try_end_b5} :catch_b5

    :catch_b5
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v2

    invoke-static {v1, v2}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


# Early track-change hook fired from PlayerService.toRestart() right after
# setDataSource(newPath) succeeds but BEFORE prepareAsync completes. By
# this point mPlayingMusic / mPlayingAudiobook already point at the new
# song so flushLocked() writes the new metadata. Moves the CT-visible
# track-change emit ~100-500 ms earlier than the OnPreparedListener path.
# Audio_id-dedup doesn't help here: restartPlay() pause()s before
# toRestart(), and pause's flushLocked has already updated mCachedAudioId.
.method public static onEarlyTrackChange()V
    .locals 3

    :try_start_e
    sget-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->onFreshTrackChange()V

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->wakeTrackChanged()V

    return-void
    :try_end_e
    .catch Ljava/lang/Throwable; {:try_start_e .. :try_end_e} :catch_e

    :catch_e
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v2

    invoke-static {v1, v2}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


# PlayerService.playerPrepared() tail hook (B5.2c). Fires AFTER the
# `iput-boolean playerIsPrepared = true` (both shutdown-restore + normal
# prepare branches). getPlayerIsPrepared() is now true, so flushLocked
# captures the freshly-valid getDuration() into mLastKnownDuration +
# y1-track-info[776..779]. Without this hook, flushLocked at
# OnPreparedListener time runs ~26 ms BEFORE playerIsPrepared flips
# and falls back to the stale prior-track mLastKnownDuration.
#
# Also re-broadcasts metachanged + playstatechanged so T5 → TRACK_CHANGED
# CHANGED and T9 → PLAYBACK_POS / STATUS CHANGED carry the corrected
# duration on the wire.
.method public static onPlayerPreparedTail()V
    .locals 3

    :try_start_pt
    sget-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->flush()V

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->wakeTrackChanged()V

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->wakePlayStateChanged()V

    return-void
    :try_end_pt
    .catch Ljava/lang/Throwable; {:try_start_pt .. :try_end_pt} :catch_pt

    :catch_pt
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v2

    invoke-static {v1, v2}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


# OnPreparedListener hook (IJK + MediaPlayer). Track has finished decoder warmup
# and is now playable — treat as track edge and consume any pending natural-end.
# After the flush, fire metachanged (wakes T5 → TRACK_CHANGED CHANGED) and
# playstatechanged (wakes T9 → PLAYBACK_POS CHANGED for the position reset).
.method public static onPrepared()V
    .locals 3

    :try_start_b5
    sget-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->onTrackEdge()V

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->wakeTrackChanged()V

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->wakePlayStateChanged()V

    return-void
    :try_end_b5
    .catch Ljava/lang/Throwable; {:try_start_b5 .. :try_end_b5} :catch_b5

    :catch_b5
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v2

    invoke-static {v1, v2}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


# OnCompletionListener hook (IJK + MediaPlayer). Player engine reached EOS.
# Latch the natural-end signal so the next onPrepared sets
# mPreviousTrackNaturalEnd, freeze the playhead at duration so T6 / T9 stop
# extrapolating past end-of-track, stop PositionTicker so we don't keep
# firing PLAYBACK_POS_CHANGED CHANGED during the prepare gap, and fire one
# final wake so the CT sees the frozen "at duration" anchor immediately.
.method public static onCompletion()V
    .locals 3

    :try_start_b5
    sget-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->markCompletion()V

    invoke-static {}, Lcom/koensayr/y1/playback/PositionTicker;->stop()V

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->wakePlayStateChanged()V

    return-void
    :try_end_b5
    .catch Ljava/lang/Throwable; {:try_start_b5 .. :try_end_b5} :catch_b5

    :catch_b5
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v2

    invoke-static {v1, v2}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


# Seek hook — prepended to PlayerService.setCurrentPosition(J)V. Forwards
# the new position to TrackInfoWriter so the live anchor refreshes and the
# CT sees PLAYBACK_POS_CHANGED CHANGED immediately on seek instead of
# waiting for the next 1 s PositionTicker tick (which would still report
# the pre-seek extrapolation).
.method public static onSeek(J)V
    .locals 3

    :try_start_seek
    sget-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    invoke-virtual {v0, p0, p1}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->onSeek(J)V

    return-void
    :try_end_seek
    .catch Ljava/lang/Throwable; {:try_start_seek .. :try_end_seek} :catch_seek

    :catch_seek
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v2

    invoke-static {v1, v2}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


# OnErrorListener hook (IJK + MediaPlayer). Clear pending natural-end since an
# error means the track was interrupted, not naturally ended.
.method public static onError()V
    .locals 3

    :try_start_b5
    sget-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->markError()V

    return-void
    :try_end_b5
    .catch Ljava/lang/Throwable; {:try_start_b5 .. :try_end_b5} :catch_b5

    :catch_b5
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v2

    invoke-static {v1, v2}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method
