.class public final Lcom/koensayr/y1/playback/PlaybackStateBridge;
.super Ljava/lang/Object;
.source "PlaybackStateBridge.smali"


# Stateless dispatcher: music-app callbacks → TrackInfoWriter mutations.
# Hooked at:
#   - Static.setPlayValue(II)V (one prepend per method body — canonical state-edge entry)
#   - PlayerService initPlayer / initPlayer2 listener lambdas (six prepends)
#
# All public methods are static; no instance state. State lives in TrackInfoWriter.


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
# Mapping mirrors Y1MediaBridge LogcatMonitor processLogLine.
.method public static onPlayValue(II)V
    .locals 2

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

    :cond_unmapped
    return-void
.end method


# OnPreparedListener hook (IJK + MediaPlayer). Track has finished decoder warmup
# and is now playable — treat as track edge and consume any pending natural-end.
.method public static onPrepared()V
    .locals 1

    sget-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->onTrackEdge()V

    return-void
.end method


# OnCompletionListener hook (IJK + MediaPlayer). Player engine reached EOS.
# Latch the natural-end signal so the next onPrepared sets mPreviousTrackNaturalEnd.
.method public static onCompletion()V
    .locals 1

    sget-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->markCompletion()V

    return-void
.end method


# OnErrorListener hook (IJK + MediaPlayer). Clear pending natural-end since an
# error means the track was interrupted, not naturally ended.
.method public static onError()V
    .locals 1

    sget-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->markError()V

    return-void
.end method
