.class public final Lcom/koensayr/y1/playback/PlaybackStateBridge;
.super Ljava/lang/Object;
.source "PlaybackStateBridge.smali"


# Stateless dispatcher: music-app callbacks → TrackInfoWriter mutations.
# Hooked at:
#   - Static.setPlayValue(II)V (one prepend per method body — canonical state-edge entry)
#   - PlayerService initPlayer / initPlayer2 listener lambdas (six prepends)
#
# Every public static method is wrapped in try/catch(Throwable) so a bug or
# unexpected state in this code path can NEVER propagate into the host method.
# The hooks are observation-only by contract: stock playback semantics must
# remain identical regardless of what we do in here. A swallowed exception
# logs a single Log.w line ("Y1Patch") and the host lambda continues.


# direct methods
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

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
    .locals 3

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

    # State-edge wake: setPlayStatus has flushed y1-track-info[792]/[780..787]
    # synchronously; fire playstatechanged so MtkBt routes through T9 and emits
    # PLAYBACK_STATUS / POS CHANGED.
    invoke-virtual {v1}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->wakePlayStateChanged()V

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
# IjkMediaPlayer / MediaPlayer setDataSource(newPath) succeeds but BEFORE
# prepareAsync's decoder warmup completes. By the time toRestart calls
# setDataSource, the music app has already updated mPlayingMusic /
# mPlayingAudiobook to the new song, so PlayerService.getPlayingSong()
# (which TrackInfoWriter.flushLocked() consults) returns the new track's
# metadata. Firing TrackInfoWriter.onFreshTrackChange() + wakeTrackChanged()
# here moves the CT-visible track-change notification ~100-500 ms earlier
# (the prepareAsync duration), so peers like Bolt see the new
# Artist/Track/Album while the audio decoder is still spinning up.
#
# onFreshTrackChange unconditionally resets position-anchor + clears the
# stale mLastKnownDuration. The audio_id-dedup path doesn't work for this
# call site because the music app's restartPlay() invokes pause() before
# toRestart(), and pause's flushLocked already updated mCachedAudioId to
# the new track's id — so by the time we'd snapshot it, old==new.
#
# When prepareAsync eventually completes the engine fires OnPreparedListener
# → onPrepared() (which calls the dedup-gated onTrackEdge — handles the
# resume-from-pause re-prepare case where audio_id is unchanged) and then
# PlayerService.playerPrepared() runs and sets playerIsPrepared=true. Our
# B5.2c hook on playerPrepared's tail then fires onPlayerPreparedTail()
# below, which re-flushes (now with getDuration() valid) and re-broadcasts
# so the post-prepare duration value reaches the CT.
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
# `iput-boolean playerIsPrepared = true` in playerPrepared() (both branches:
# the shutdown-restore branch + the normal prepare branch). At this point
# PlayerService.getPlayerIsPrepared() returns true, so a fresh flush
# captures the newly-valid getDuration() value into mLastKnownDuration and
# y1-track-info[776..779]. Without this hook, flushLocked at OnPreparedListener
# time runs ~26 ms BEFORE playerIsPrepared flips, so it falls back to the
# stale prior-track mLastKnownDuration and the new-track duration only
# reaches the CT on the next state-edge (often ~one track late — verified
# Kia 2026-05-14: dur=165120 leaked across tracks 1/2/3, dur=126407 across
# 4/5, etc.).
#
# Also re-broadcasts metachanged + playstatechanged so T5 → TRACK_CHANGED
# CHANGED and T9 → PLAYBACK_POS / STATUS CHANGED carry the now-correct
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
