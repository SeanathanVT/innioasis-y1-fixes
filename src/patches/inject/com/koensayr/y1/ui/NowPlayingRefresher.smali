.class public final Lcom/koensayr/y1/ui/NowPlayingRefresher;
.super Ljava/lang/Object;
.implements Ljava/lang/Runnable;
.source "NowPlayingRefresher.smali"


# Dispatches a UI refresh to the currently visible MusicPlayerActivity when
# Repeat / Shuffle SharedPreferences change (Y1-UI-driven OR CT-driven via
# T_papp 0x14 → PappSetFileObserver → SharedPreferencesUtils.setMusic*).
# Stock music app re-reads SharedPreferences only in MusicPlayerActivity.
# onResume(), so a CT-initiated change wouldn't visually reflect on the
# Now Playing screen until the user navigated away and back — this patch
# closes that gap to match iPhone/Pixel-class MediaSession behaviour.


.field public static final INSTANCE:Lcom/koensayr/y1/ui/NowPlayingRefresher;

# Currently visible Now Playing activity (set by onResume, cleared by
# matching onPause). Strong reference — but the matched-clear in onPause
# means the activity's lifecycle drives the clear, so no leak.
.field private static sCurrent:Lcom/innioasis/music/MusicPlayerActivity;


.method static constructor <clinit>()V
    .locals 1

    new-instance v0, Lcom/koensayr/y1/ui/NowPlayingRefresher;

    invoke-direct {v0}, Lcom/koensayr/y1/ui/NowPlayingRefresher;-><init>()V

    sput-object v0, Lcom/koensayr/y1/ui/NowPlayingRefresher;->INSTANCE:Lcom/koensayr/y1/ui/NowPlayingRefresher;

    return-void
.end method

.method private constructor <init>()V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


# Called from MusicPlayerActivity.onResume. Tracks the foreground activity
# so refresh() can reach it.
.method public static onResume(Lcom/innioasis/music/MusicPlayerActivity;)V
    .locals 0

    sput-object p0, Lcom/koensayr/y1/ui/NowPlayingRefresher;->sCurrent:Lcom/innioasis/music/MusicPlayerActivity;

    return-void
.end method


# Called from MusicPlayerActivity.onPause. Clears the tracked activity only
# if the paused instance matches (defensive against overlapping create /
# resume / pause cycles during configuration changes).
.method public static onPause(Lcom/innioasis/music/MusicPlayerActivity;)V
    .locals 1

    sget-object v0, Lcom/koensayr/y1/ui/NowPlayingRefresher;->sCurrent:Lcom/innioasis/music/MusicPlayerActivity;

    if-ne v0, p0, :cond_not_current

    const/4 v0, 0x0

    sput-object v0, Lcom/koensayr/y1/ui/NowPlayingRefresher;->sCurrent:Lcom/innioasis/music/MusicPlayerActivity;

    :cond_not_current
    return-void
.end method


# Trigger a UI refresh on the currently visible MusicPlayerActivity, if any.
# Called from PappStateBroadcaster.sendNow on every Repeat / Shuffle edge.
# Posts the singleton Runnable via Activity.runOnUiThread to ensure the
# refreshUI() call lands on the main thread regardless of which thread
# fired the SharedPreferences listener.
.method public static refresh()V
    .locals 2

    :try_start_0
    sget-object v0, Lcom/koensayr/y1/ui/NowPlayingRefresher;->sCurrent:Lcom/innioasis/music/MusicPlayerActivity;

    if-eqz v0, :cond_no_current

    sget-object v1, Lcom/koensayr/y1/ui/NowPlayingRefresher;->INSTANCE:Lcom/koensayr/y1/ui/NowPlayingRefresher;

    invoke-virtual {v0, v1}, Lcom/innioasis/music/MusicPlayerActivity;->runOnUiThread(Ljava/lang/Runnable;)V

    :cond_no_current
    return-void
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    :catch_0
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v0

    invoke-static {v1, v0}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


# Runnable callback: dispatched via Activity.runOnUiThread() from refresh().
# Re-checks sCurrent because the activity may have paused between refresh()
# and the UI-thread dispatch.
.method public run()V
    .locals 2

    :try_start_0
    sget-object v0, Lcom/koensayr/y1/ui/NowPlayingRefresher;->sCurrent:Lcom/innioasis/music/MusicPlayerActivity;

    if-eqz v0, :cond_no_current

    invoke-virtual {v0}, Lcom/innioasis/music/MusicPlayerActivity;->refreshUI()V

    :cond_no_current
    return-void
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    :catch_0
    move-exception v0

    const-string v1, "Y1Patch"

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v0

    invoke-static {v1, v0}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method
