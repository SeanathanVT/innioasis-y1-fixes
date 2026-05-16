.class public Lcom/koensayr/y1/trackinfo/DbgPostReadRunnable;
.super Ljava/lang/Object;
.implements Ljava/lang/Runnable;
.source "DbgPostReadRunnable.smali"


# Debug-only one-shot Runnable for the wakeTrackChanged / wakePlayStateChanged
# post-broadcast trampoline-state read. Allocated per call from
# TrackInfoWriter._dbgPostReadAfter(tag, delayMs); the Handler's postDelayed
# runs us on the main thread ~50 ms after the broadcast goes out, by which
# time MtkBt.odex's BluetoothAvrcpReceiver has dispatched + T5/T9 have either
# emitted CHANGED (and cleared the relevant §6.7.1 gate byte) or been gated
# out (gate stays armed). Together with the pre-broadcast log emitted from
# wakeTrackChanged / wakePlayStateChanged head, this lets a single tag pair
# answer "did T5/T9 actually fire?" without waiting for the next wake.
#
# Class only ships when patch_y1_apk.py is run with KOENSAYR_DEBUG=1.


# instance fields
.field private final mTag:Ljava/lang/String;


# direct methods
.method public constructor <init>(Ljava/lang/String;)V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    iput-object p1, p0, Lcom/koensayr/y1/trackinfo/DbgPostReadRunnable;->mTag:Ljava/lang/String;

    return-void
.end method


# virtual methods
.method public run()V
    .locals 4

    :try_start_0
    new-instance v0, Ljava/lang/StringBuilder;

    invoke-direct {v0}, Ljava/lang/StringBuilder;-><init>()V

    iget-object v1, p0, Lcom/koensayr/y1/trackinfo/DbgPostReadRunnable;->mTag:Ljava/lang/String;

    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string v1, ".post"

    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v0

    invoke-static {v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->_dbgLogTrampolineState(Ljava/lang/String;)V
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    return-void

    :catch_0
    move-exception v0

    return-void
.end method
