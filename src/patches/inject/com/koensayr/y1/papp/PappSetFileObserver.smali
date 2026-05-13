.class public final Lcom/koensayr/y1/papp/PappSetFileObserver;
.super Landroid/os/FileObserver;
.source "PappSetFileObserver.smali"


# Watches /data/data/com.innioasis.y1/files/y1-papp-set for CLOSE_WRITE.
# T_papp 0x14 in libextavrcp_jni.so writes this file on every CT-initiated
# PApp Set; the observer reads the 2 bytes (AVRCP attr_id, AVRCP value), maps
# to Y1 enum, and calls SharedPreferencesUtils.setMusicRepeatMode /
# setMusicIsShuffle directly (no Intent hop — same process).


# static fields
.field private static sInstance:Lcom/koensayr/y1/papp/PappSetFileObserver;

.field private static sFile:Ljava/io/File;


# instance fields
.field private final mFile:Ljava/io/File;


# direct methods
.method public constructor <init>(Ljava/io/File;)V
    .locals 2

    invoke-virtual {p1}, Ljava/io/File;->getPath()Ljava/lang/String;

    move-result-object v0

    const/16 v1, 0x8

    invoke-direct {p0, v0, v1}, Landroid/os/FileObserver;-><init>(Ljava/lang/String;I)V

    iput-object p1, p0, Lcom/koensayr/y1/papp/PappSetFileObserver;->mFile:Ljava/io/File;

    return-void
.end method


# Idempotent registration. Called from Y1Application.onCreate AFTER
# TrackInfoWriter.init has prepared the watched file (ensureFile creates a
# zero-byte y1-papp-set so FileObserver can attach to an existing path).
.method public static start(Landroid/content/Context;)V
    .locals 4

    sget-object v0, Lcom/koensayr/y1/papp/PappSetFileObserver;->sInstance:Lcom/koensayr/y1/papp/PappSetFileObserver;

    if-eqz v0, :cond_first

    return-void

    :cond_first
    invoke-virtual {p0}, Landroid/content/Context;->getFilesDir()Ljava/io/File;

    move-result-object v0

    if-nez v0, :cond_have_dir

    return-void

    :cond_have_dir
    new-instance v1, Ljava/io/File;

    const-string v2, "y1-papp-set"

    invoke-direct {v1, v0, v2}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    sput-object v1, Lcom/koensayr/y1/papp/PappSetFileObserver;->sFile:Ljava/io/File;

    new-instance v2, Lcom/koensayr/y1/papp/PappSetFileObserver;

    invoke-direct {v2, v1}, Lcom/koensayr/y1/papp/PappSetFileObserver;-><init>(Ljava/io/File;)V

    sput-object v2, Lcom/koensayr/y1/papp/PappSetFileObserver;->sInstance:Lcom/koensayr/y1/papp/PappSetFileObserver;

    invoke-virtual {v2}, Lcom/koensayr/y1/papp/PappSetFileObserver;->startWatching()V

    return-void
.end method


# virtual methods
.method public onEvent(ILjava/lang/String;)V
    .locals 6

    iget-object v0, p0, Lcom/koensayr/y1/papp/PappSetFileObserver;->mFile:Ljava/io/File;

    if-nez v0, :cond_have_file

    return-void

    :cond_have_file
    const/4 v1, 0x0

    const/4 v2, 0x0

    :try_start_0
    new-instance v3, Ljava/io/FileInputStream;

    invoke-direct {v3, v0}, Ljava/io/FileInputStream;-><init>(Ljava/io/File;)V

    move-object v1, v3

    const/4 v3, 0x2

    new-array v3, v3, [B

    invoke-virtual {v1, v3}, Ljava/io/FileInputStream;->read([B)I

    move-result v4

    const/4 v5, 0x2

    if-ge v4, v5, :cond_full_read

    invoke-virtual {v1}, Ljava/io/FileInputStream;->close()V

    return-void

    :cond_full_read
    const/4 v4, 0x0

    aget-byte v4, v3, v4

    and-int/lit16 v4, v4, 0xff

    const/4 v5, 0x1

    aget-byte v5, v3, v5

    and-int/lit16 v5, v5, 0xff

    invoke-virtual {v1}, Ljava/io/FileInputStream;->close()V

    invoke-static {v4, v5}, Lcom/koensayr/y1/papp/PappSetFileObserver;->dispatch(II)V
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    return-void

    :catch_0
    move-exception v3

    if-eqz v1, :cond_no_close

    :try_start_1
    invoke-virtual {v1}, Ljava/io/FileInputStream;->close()V
    :try_end_1
    .catch Ljava/lang/Throwable; {:try_start_1 .. :try_end_1} :catch_1

    :catch_1
    :cond_no_close
    return-void
.end method


# Map (AVRCP attr_id, AVRCP value) → Y1 SharedPreferences setter.
#   attr 0x02 = Repeat:  0x01→0(OFF), 0x02→1(ONE), 0x03→2(ALL)
#   attr 0x03 = Shuffle: 0x01→false(OFF), 0x02→true(ALL_TRACK)
.method private static dispatch(II)V
    .locals 3

    const/4 v0, 0x2

    if-ne p0, v0, :cond_shuffle

    # Repeat
    const/4 v0, 0x1

    if-ne p1, v0, :cond_rep_one

    const/4 v0, 0x0

    goto :goto_set_repeat

    :cond_rep_one
    const/4 v0, 0x2

    if-ne p1, v0, :cond_rep_all

    const/4 v0, 0x1

    goto :goto_set_repeat

    :cond_rep_all
    const/4 v0, 0x3

    if-ne p1, v0, :cond_unsupported

    const/4 v0, 0x2

    :goto_set_repeat
    sget-object v1, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->INSTANCE:Lcom/innioasis/y1/utils/SharedPreferencesUtils;

    invoke-virtual {v1, v0}, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->setMusicRepeatMode(I)V

    return-void

    :cond_shuffle
    const/4 v0, 0x3

    if-ne p0, v0, :cond_unsupported

    const/4 v0, 0x1

    if-ne p1, v0, :cond_shf_on

    const/4 v0, 0x0

    goto :goto_set_shuffle

    :cond_shf_on
    const/4 v0, 0x2

    if-ne p1, v0, :cond_unsupported

    const/4 v0, 0x1

    :goto_set_shuffle
    sget-object v1, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->INSTANCE:Lcom/innioasis/y1/utils/SharedPreferencesUtils;

    invoke-virtual {v1, v0}, Lcom/innioasis/y1/utils/SharedPreferencesUtils;->setMusicIsShuffle(Z)V

    :cond_unsupported
    return-void
.end method
