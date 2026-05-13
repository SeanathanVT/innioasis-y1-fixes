.class public final Lcom/koensayr/y1/playback/PositionTicker;
.super Ljava/lang/Object;
.implements Ljava/lang/Runnable;
.source "PositionTicker.smali"


# Drives the 1-second PLAYBACK_POS_CHANGED CHANGED cadence required by AVRCP
# 1.3 §5.4.2 Tbl 5.33 (Optional, ICS Table 7 row 27). While the music app is
# in PLAYING state, this Runnable re-posts itself every 1000 ms and fires
# com.android.music.playstatechanged via TrackInfoWriter.wakePlayStateChanged().
# That wakes MtkBt.odex's cardinality-NOP-patched dispatch →
# notificationPlayStatusChangedNative → T9, which reads the live-extrapolated
# position via clock_gettime(CLOCK_BOOTTIME) and emits PLAYBACK_POS_CHANGED
# CHANGED. PlaybackStateBridge.onPlayValue calls start() on PLAYING edges and
# stop() on PAUSED/STOPPED edges (canonical state-edge entry via
# Static.setPlayValue).


# static fields
.field private static final INSTANCE:Lcom/koensayr/y1/playback/PositionTicker;

.field private static sHandler:Landroid/os/Handler;


# direct methods
.method static constructor <clinit>()V
    .locals 1

    new-instance v0, Lcom/koensayr/y1/playback/PositionTicker;

    invoke-direct {v0}, Lcom/koensayr/y1/playback/PositionTicker;-><init>()V

    sput-object v0, Lcom/koensayr/y1/playback/PositionTicker;->INSTANCE:Lcom/koensayr/y1/playback/PositionTicker;

    return-void
.end method


.method private constructor <init>()V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


# Cancel any pending tick + post a fresh one 1000 ms out. Idempotent: re-calls
# replace the queued callback. Lazily instantiates the main-thread Handler on
# first call (constructor avoids touching Looper because the singleton may be
# initialized before the main Looper exists in some startup paths).
.method public static start()V
    .locals 4

    :try_start_0
    sget-object v0, Lcom/koensayr/y1/playback/PositionTicker;->sHandler:Landroid/os/Handler;

    if-nez v0, :cond_have_handler

    new-instance v0, Landroid/os/Handler;

    invoke-static {}, Landroid/os/Looper;->getMainLooper()Landroid/os/Looper;

    move-result-object v1

    invoke-direct {v0, v1}, Landroid/os/Handler;-><init>(Landroid/os/Looper;)V

    sput-object v0, Lcom/koensayr/y1/playback/PositionTicker;->sHandler:Landroid/os/Handler;

    :cond_have_handler
    sget-object v1, Lcom/koensayr/y1/playback/PositionTicker;->INSTANCE:Lcom/koensayr/y1/playback/PositionTicker;

    invoke-virtual {v0, v1}, Landroid/os/Handler;->removeCallbacks(Ljava/lang/Runnable;)V

    const-wide/16 v2, 0x3e8

    invoke-virtual {v0, v1, v2, v3}, Landroid/os/Handler;->postDelayed(Ljava/lang/Runnable;J)Z

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


.method public static stop()V
    .locals 2

    :try_start_0
    sget-object v0, Lcom/koensayr/y1/playback/PositionTicker;->sHandler:Landroid/os/Handler;

    if-eqz v0, :cond_no_handler

    sget-object v1, Lcom/koensayr/y1/playback/PositionTicker;->INSTANCE:Lcom/koensayr/y1/playback/PositionTicker;

    invoke-virtual {v0, v1}, Landroid/os/Handler;->removeCallbacks(Ljava/lang/Runnable;)V

    :cond_no_handler
    return-void
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    :catch_0
    move-exception v0

    return-void
.end method


# Runnable.run() — fired by the Handler on the main thread every 1000 ms while
# the ticker is active. Fires wakePlayStateChanged (drives T9 →
# PLAYBACK_POS_CHANGED with the live-extrapolated position) then re-posts.
.method public run()V
    .locals 4

    :try_start_0
    sget-object v0, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    invoke-virtual {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->wakePlayStateChanged()V

    sget-object v0, Lcom/koensayr/y1/playback/PositionTicker;->sHandler:Landroid/os/Handler;

    if-eqz v0, :cond_no_handler

    const-wide/16 v2, 0x3e8

    invoke-virtual {v0, p0, v2, v3}, Landroid/os/Handler;->postDelayed(Ljava/lang/Runnable;J)Z

    :cond_no_handler
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
