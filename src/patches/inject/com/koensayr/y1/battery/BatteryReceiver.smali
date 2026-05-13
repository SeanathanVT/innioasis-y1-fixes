.class public final Lcom/koensayr/y1/battery/BatteryReceiver;
.super Landroid/content/BroadcastReceiver;
.source "BatteryReceiver.smali"


# Receives Intent.ACTION_BATTERY_CHANGED, bucket-maps to AVRCP §5.4.2 Tbl 5.35
# enum, calls TrackInfoWriter.setBattery (which dedupes on bucket transition
# and only flushes the file when the bucket actually changes).
#
# Mapping:
#   STATUS_FULL                    → 4 FULL_CHARGE
#   PLUGGED (AC | USB | wireless)  → 3 EXTERNAL
#   level <= 15                    → 2 CRITICAL
#   level <= 30                    → 1 WARNING
#   else                           → 0 NORMAL
#
# Self-rooted via static field so the receiver isn't GC'd. Registered from
# Y1Application.onCreate. Sticky-broadcast value is processed inline at
# registration time so cold boot has a real bucket before the next CHANGED tick.


# static fields
.field private static sInstance:Lcom/koensayr/y1/battery/BatteryReceiver;


# direct methods
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/content/BroadcastReceiver;-><init>()V

    return-void
.end method


# Idempotent registration — called from Y1Application.onCreate.
.method public static register(Landroid/content/Context;)V
    .locals 5

    sget-object v0, Lcom/koensayr/y1/battery/BatteryReceiver;->sInstance:Lcom/koensayr/y1/battery/BatteryReceiver;

    if-eqz v0, :cond_first

    return-void

    :cond_first
    new-instance v0, Lcom/koensayr/y1/battery/BatteryReceiver;

    invoke-direct {v0}, Lcom/koensayr/y1/battery/BatteryReceiver;-><init>()V

    sput-object v0, Lcom/koensayr/y1/battery/BatteryReceiver;->sInstance:Lcom/koensayr/y1/battery/BatteryReceiver;

    new-instance v1, Landroid/content/IntentFilter;

    const-string v2, "android.intent.action.BATTERY_CHANGED"

    invoke-direct {v1, v2}, Landroid/content/IntentFilter;-><init>(Ljava/lang/String;)V

    invoke-virtual {p0, v0, v1}, Landroid/content/Context;->registerReceiver(Landroid/content/BroadcastReceiver;Landroid/content/IntentFilter;)Landroid/content/Intent;

    move-result-object v2

    if-eqz v2, :cond_no_sticky

    invoke-static {v2}, Lcom/koensayr/y1/battery/BatteryReceiver;->bucketFromIntent(Landroid/content/Intent;)B

    move-result v3

    sget-object v4, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    int-to-byte v3, v3

    invoke-virtual {v4, v3}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->setBattery(B)V

    :cond_no_sticky
    return-void
.end method


.method private static bucketFromIntent(Landroid/content/Intent;)B
    .locals 5

    const-string v0, "level"

    const/4 v1, -0x1

    invoke-virtual {p0, v0, v1}, Landroid/content/Intent;->getIntExtra(Ljava/lang/String;I)I

    move-result v0

    const-string v2, "scale"

    const/16 v3, 0x64

    invoke-virtual {p0, v2, v3}, Landroid/content/Intent;->getIntExtra(Ljava/lang/String;I)I

    move-result v2

    const-string v3, "plugged"

    const/4 v4, 0x0

    invoke-virtual {p0, v3, v4}, Landroid/content/Intent;->getIntExtra(Ljava/lang/String;I)I

    move-result v3

    const-string v4, "status"

    const/4 v1, 0x1

    invoke-virtual {p0, v4, v1}, Landroid/content/Intent;->getIntExtra(Ljava/lang/String;I)I

    move-result p0

    # pct = (level >= 0 && scale > 0) ? level*100/scale : -1
    const/4 v1, -0x1

    if-ltz v0, :cond_no_pct

    if-lez v2, :cond_no_pct

    mul-int/lit8 v0, v0, 0x64

    div-int v1, v0, v2

    :cond_no_pct
    # bucket order: FULL_CHARGE > EXTERNAL > CRITICAL > WARNING > NORMAL
    const/4 v0, 0x5

    if-ne p0, v0, :cond_not_full

    const/4 v0, 0x4

    return v0

    :cond_not_full
    if-eqz v3, :cond_not_plugged

    const/4 v0, 0x3

    return v0

    :cond_not_plugged
    if-ltz v1, :cond_no_pct_check

    const/16 v0, 0xf

    if-gt v1, v0, :cond_not_critical

    const/4 v0, 0x2

    return v0

    :cond_not_critical
    const/16 v0, 0x1e

    if-gt v1, v0, :cond_no_pct_check

    const/4 v0, 0x1

    return v0

    :cond_no_pct_check
    const/4 v0, 0x0

    return v0
.end method


# virtual methods
.method public onReceive(Landroid/content/Context;Landroid/content/Intent;)V
    .locals 3

    if-nez p2, :cond_have

    return-void

    :cond_have
    invoke-static {p2}, Lcom/koensayr/y1/battery/BatteryReceiver;->bucketFromIntent(Landroid/content/Intent;)B

    move-result v0

    sget-object v1, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->INSTANCE:Lcom/koensayr/y1/trackinfo/TrackInfoWriter;

    int-to-byte v0, v0

    invoke-virtual {v1, v0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->setBattery(B)V

    # Fire `com.android.music.playstatechanged` so MtkBt's BluetoothAvrcpReceiver
    # wakes notificationPlayStatusChangedNative → T9, which reads y1-track-info[794]
    # and emits AVRCP BATT_STATUS_CHANGED CHANGED on the wire when the bucket flips.
    :try_start
    new-instance v1, Landroid/content/Intent;

    const-string v2, "com.android.music.playstatechanged"

    invoke-direct {v1, v2}, Landroid/content/Intent;-><init>(Ljava/lang/String;)V

    invoke-virtual {p1, v1}, Landroid/content/Context;->sendBroadcast(Landroid/content/Intent;)V
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch

    return-void

    :catch
    move-exception v1

    const-string v2, "Y1Patch"

    invoke-virtual {v1}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v0

    invoke-static {v2, v0}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method
