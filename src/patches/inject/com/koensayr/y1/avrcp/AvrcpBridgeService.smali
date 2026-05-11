.class public Lcom/koensayr/y1/avrcp/AvrcpBridgeService;
.super Landroid/app/Service;
.source "AvrcpBridgeService.smali"


# Bound AVRCP-music service replacing Y1MediaBridge's MediaBridgeService.
# The music app's AndroidManifest declares this service with two intent-filter
# actions:
#   com.android.music.MediaPlaybackService    (priority 100)
#   com.android.music.IMediaPlaybackService
# so MtkBt's BTAvrcpMusicAdapter.bindService resolves here.
#
# The trampoline chain in libextavrcp_jni.so reads y1-track-info directly and
# responds to inbound AVRCP PDUs without transacting with this Binder. The
# Binder's role here is narrow:
#   1. onBind returns an IBinder so MtkBt's mMusicService becomes non-null.
#   2. registerCallback (transact code 1) stashes MtkBt's callback IBinder so
#      we can wake notificationPlayStatusChangedNative / TrackChangedNative.
#   3. getCapabilities (code 5) advertises EVENT_PLAYBACK_STATUS_CHANGED (0x01)
#      and EVENT_TRACK_CHANGED (0x02) so MtkBt actually registers.
#   4. All other codes ack-only (writeNoException + return true) — the C-side
#      trampoline chain is what the CT actually reads on the wire.
#
# Threading: callback list is a CopyOnWriteArrayList stored in a static field.
# Reads are O(N) iteration; writes are infrequent (one bind/unbind per BT
# enable cycle). Static so callers don't need to walk back through the service
# binding to fire notifications.


# static fields
.field private static volatile sCallbacks:Ljava/util/concurrent/CopyOnWriteArrayList;
.field private static volatile sInstance:Lcom/koensayr/y1/avrcp/AvrcpBridgeService;


# instance fields
.field private mBinder:Lcom/koensayr/y1/avrcp/AvrcpBinder;


# direct methods

.method static constructor <clinit>()V
    .locals 1

    new-instance v0, Ljava/util/concurrent/CopyOnWriteArrayList;

    invoke-direct {v0}, Ljava/util/concurrent/CopyOnWriteArrayList;-><init>()V

    sput-object v0, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sCallbacks:Ljava/util/concurrent/CopyOnWriteArrayList;

    return-void
.end method

.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/app/Service;-><init>()V

    return-void
.end method


# Public static: fire IBTAvrcpMusicCallback.notifyPlaybackStatus (transact 1)
# on every registered callback. Called from PlaybackStateBridge after every
# setPlayStatus edge so MtkBt's notificationPlayStatusChangedNative fires →
# T9 emits AVRCP PLAYBACK_STATUS_CHANGED CHANGED on the wire.
#
# Param: status byte per IBTAvrcpMusicCallback contract:
#   1 = stopped, 2 = playing, 3 = paused.
# Caller is responsible for the AVRCP→callback enum mapping.
.method public static notifyPlaybackStatus(B)V
    .locals 1

    const/4 v0, 0x1

    invoke-static {v0, p0}, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->dispatchCallback(IB)V

    return-void
.end method


# Public static: fire IBTAvrcpMusicCallback.notifyTrackChanged (transact 2)
# on every registered callback. Called from PlaybackStateBridge.onTrackEdge
# (i.e. on every metachanged) so notificationTrackChangedNative fires → T5
# emits the AVRCP TRACK_CHANGED 3-tuple on the wire.
.method public static notifyTrackChanged(J)V
    .locals 1

    const/4 v0, 0x2

    invoke-static {v0, p0, p1}, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->dispatchCallback(IJ)V

    return-void
.end method


# Dispatch a 1-byte payload callback (used by notifyPlaybackStatus, code 1).
.method private static dispatchCallback(IB)V
    .locals 7

    sget-object v0, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sCallbacks:Ljava/util/concurrent/CopyOnWriteArrayList;

    invoke-virtual {v0}, Ljava/util/concurrent/CopyOnWriteArrayList;->iterator()Ljava/util/Iterator;

    move-result-object v0

    :loop
    invoke-interface {v0}, Ljava/util/Iterator;->hasNext()Z

    move-result v1

    if-eqz v1, :end

    invoke-interface {v0}, Ljava/util/Iterator;->next()Ljava/lang/Object;

    move-result-object v1

    check-cast v1, Landroid/os/IBinder;

    invoke-static {}, Landroid/os/Parcel;->obtain()Landroid/os/Parcel;

    move-result-object v2

    invoke-static {}, Landroid/os/Parcel;->obtain()Landroid/os/Parcel;

    move-result-object v3

    :try_start
    const-string v4, "com.mediatek.bluetooth.avrcp.IBTAvrcpMusicCallback"

    invoke-virtual {v2, v4}, Landroid/os/Parcel;->writeInterfaceToken(Ljava/lang/String;)V

    invoke-virtual {v2, p1}, Landroid/os/Parcel;->writeByte(B)V

    const/4 v4, 0x0

    invoke-interface {v1, p0, v2, v3, v4}, Landroid/os/IBinder;->transact(ILandroid/os/Parcel;Landroid/os/Parcel;I)Z
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch

    :recycle
    invoke-virtual {v3}, Landroid/os/Parcel;->recycle()V

    invoke-virtual {v2}, Landroid/os/Parcel;->recycle()V

    goto :loop

    :catch
    move-exception v4

    sget-object v5, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sCallbacks:Ljava/util/concurrent/CopyOnWriteArrayList;

    invoke-virtual {v5, v1}, Ljava/util/concurrent/CopyOnWriteArrayList;->remove(Ljava/lang/Object;)Z

    const-string v5, "Y1Patch"

    invoke-virtual {v4}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v6

    invoke-static {v5, v6}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    goto :recycle

    :end
    return-void
.end method


# Dispatch an 8-byte (long) payload callback (used by notifyTrackChanged, code 2).
.method private static dispatchCallback(IJ)V
    .locals 8

    sget-object v0, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sCallbacks:Ljava/util/concurrent/CopyOnWriteArrayList;

    invoke-virtual {v0}, Ljava/util/concurrent/CopyOnWriteArrayList;->iterator()Ljava/util/Iterator;

    move-result-object v0

    :loop
    invoke-interface {v0}, Ljava/util/Iterator;->hasNext()Z

    move-result v1

    if-eqz v1, :end

    invoke-interface {v0}, Ljava/util/Iterator;->next()Ljava/lang/Object;

    move-result-object v1

    check-cast v1, Landroid/os/IBinder;

    invoke-static {}, Landroid/os/Parcel;->obtain()Landroid/os/Parcel;

    move-result-object v2

    invoke-static {}, Landroid/os/Parcel;->obtain()Landroid/os/Parcel;

    move-result-object v3

    :try_start
    const-string v4, "com.mediatek.bluetooth.avrcp.IBTAvrcpMusicCallback"

    invoke-virtual {v2, v4}, Landroid/os/Parcel;->writeInterfaceToken(Ljava/lang/String;)V

    invoke-virtual {v2, p1, p2}, Landroid/os/Parcel;->writeLong(J)V

    const/4 v4, 0x0

    invoke-interface {v1, p0, v2, v3, v4}, Landroid/os/IBinder;->transact(ILandroid/os/Parcel;Landroid/os/Parcel;I)Z
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch

    :recycle
    invoke-virtual {v3}, Landroid/os/Parcel;->recycle()V

    invoke-virtual {v2}, Landroid/os/Parcel;->recycle()V

    goto :loop

    :catch
    move-exception v4

    sget-object v5, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sCallbacks:Ljava/util/concurrent/CopyOnWriteArrayList;

    invoke-virtual {v5, v1}, Ljava/util/concurrent/CopyOnWriteArrayList;->remove(Ljava/lang/Object;)Z

    const-string v5, "Y1Patch"

    invoke-virtual {v4}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v6

    invoke-static {v5, v6}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    goto :recycle

    :end
    return-void
.end method


# Package-private: callback (un)registration accessors used by AvrcpBinder.
.method static addCallback(Landroid/os/IBinder;)V
    .locals 2

    if-nez p0, :nn

    return-void

    :nn
    sget-object v0, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sCallbacks:Ljava/util/concurrent/CopyOnWriteArrayList;

    invoke-virtual {v0, p0}, Ljava/util/concurrent/CopyOnWriteArrayList;->contains(Ljava/lang/Object;)Z

    move-result v1

    if-nez v1, :dup

    invoke-virtual {v0, p0}, Ljava/util/concurrent/CopyOnWriteArrayList;->add(Ljava/lang/Object;)Z

    :dup
    return-void
.end method


.method static removeCallback(Landroid/os/IBinder;)V
    .locals 1

    if-nez p0, :nn

    return-void

    :nn
    sget-object v0, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sCallbacks:Ljava/util/concurrent/CopyOnWriteArrayList;

    invoke-virtual {v0, p0}, Ljava/util/concurrent/CopyOnWriteArrayList;->remove(Ljava/lang/Object;)Z

    return-void
.end method


# Send a (DOWN, UP) media-key pair to the music app's PlayControllerReceiver.
# Used by AvrcpBinder.onTransact for transport-control codes (play / pause /
# stop / next / prev). Matches Y1MediaBridge's sendMediaKey shape exactly so
# Patch E's discrete dispatch path handles them.
.method static sendMediaKey(I)V
    .locals 6

    sget-object v0, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sInstance:Lcom/koensayr/y1/avrcp/AvrcpBridgeService;

    if-nez v0, :nn

    return-void

    :nn
    :try_start
    new-instance v1, Landroid/content/ComponentName;

    const-string v2, "com.innioasis.y1"

    const-string v3, "com.innioasis.y1.receiver.PlayControllerReceiver"

    invoke-direct {v1, v2, v3}, Landroid/content/ComponentName;-><init>(Ljava/lang/String;Ljava/lang/String;)V

    const-string v4, "android.intent.action.MEDIA_BUTTON"

    # DOWN
    new-instance v2, Landroid/content/Intent;

    invoke-direct {v2, v4}, Landroid/content/Intent;-><init>(Ljava/lang/String;)V

    invoke-virtual {v2, v1}, Landroid/content/Intent;->setComponent(Landroid/content/ComponentName;)Landroid/content/Intent;

    new-instance v3, Landroid/view/KeyEvent;

    const/4 v5, 0x0

    invoke-direct {v3, v5, p0}, Landroid/view/KeyEvent;-><init>(II)V

    const-string v5, "android.intent.extra.KEY_EVENT"

    invoke-virtual {v2, v5, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Landroid/os/Parcelable;)Landroid/content/Intent;

    invoke-virtual {v0, v2}, Landroid/app/Service;->sendBroadcast(Landroid/content/Intent;)V

    # UP
    new-instance v2, Landroid/content/Intent;

    invoke-direct {v2, v4}, Landroid/content/Intent;-><init>(Ljava/lang/String;)V

    invoke-virtual {v2, v1}, Landroid/content/Intent;->setComponent(Landroid/content/ComponentName;)Landroid/content/Intent;

    new-instance v3, Landroid/view/KeyEvent;

    const/4 v5, 0x1

    invoke-direct {v3, v5, p0}, Landroid/view/KeyEvent;-><init>(II)V

    const-string v5, "android.intent.extra.KEY_EVENT"

    invoke-virtual {v2, v5, v3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Landroid/os/Parcelable;)Landroid/content/Intent;

    invoke-virtual {v0, v2}, Landroid/app/Service;->sendBroadcast(Landroid/content/Intent;)V
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch

    return-void

    :catch
    move-exception v1

    const-string v2, "Y1Patch"

    invoke-virtual {v1}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v3

    invoke-static {v2, v3}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


# virtual methods

.method public onBind(Landroid/content/Intent;)Landroid/os/IBinder;
    .locals 1

    iget-object v0, p0, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->mBinder:Lcom/koensayr/y1/avrcp/AvrcpBinder;

    return-object v0
.end method


.method public onCreate()V
    .locals 1

    invoke-super {p0}, Landroid/app/Service;->onCreate()V

    new-instance v0, Lcom/koensayr/y1/avrcp/AvrcpBinder;

    invoke-direct {v0}, Lcom/koensayr/y1/avrcp/AvrcpBinder;-><init>()V

    iput-object v0, p0, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->mBinder:Lcom/koensayr/y1/avrcp/AvrcpBinder;

    sput-object p0, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sInstance:Lcom/koensayr/y1/avrcp/AvrcpBridgeService;

    const-string v0, "Y1Patch"

    const-string v1, "AvrcpBridgeService.onCreate"

    invoke-static {v0, v1}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method


.method public onDestroy()V
    .locals 1

    const/4 v0, 0x0

    sput-object v0, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sInstance:Lcom/koensayr/y1/avrcp/AvrcpBridgeService;

    sget-object v0, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sCallbacks:Ljava/util/concurrent/CopyOnWriteArrayList;

    invoke-virtual {v0}, Ljava/util/concurrent/CopyOnWriteArrayList;->clear()V

    invoke-super {p0}, Landroid/app/Service;->onDestroy()V

    return-void
.end method


.method public onUnbind(Landroid/content/Intent;)Z
    .locals 1

    const/4 v0, 0x1

    return v0
.end method
