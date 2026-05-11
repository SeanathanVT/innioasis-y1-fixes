.class public Lcom/koensayr/y1/avrcp/AvrcpBinder;
.super Landroid/os/Binder;
.source "AvrcpBinder.smali"


# Minimal IBTAvrcpMusic + IMediaPlaybackService Binder for MtkBt's
# BTAvrcpMusicAdapter. The C-side trampoline chain in libextavrcp_jni.so reads
# y1-track-info directly from disk for every CT-visible PDU; this Binder's
# role is narrow:
#
#   - Be reachable via bindService so MtkBt's mMusicService is non-null and
#     sPlayServiceInterface gets set.
#   - Code 1 (registerCallback): stash MtkBt's IBTAvrcpMusicCallback IBinder so
#     PlaybackStateBridge / PappStateBroadcaster / BatteryReceiver can fire
#     notifyPlaybackStatus / notifyTrackChanged that wake T5 / T9 natively.
#   - Code 2 (unregisterCallback): remove from set.
#   - Code 5 (getCapabilities): advertise {0x01 PLAYBACK_STATUS_CHANGED, 0x02
#     TRACK_CHANGED} so MtkBt's adapter actually issues REGISTER_NOTIFICATION
#     for those events.
#   - Transport keys (codes 6..13 on IBTAvrcpMusic): broadcast media keys to
#     PlayControllerReceiver — handles play / pause / stop / next / prev /
#     prevGroup / nextGroup. The trampoline chain handles AVRCP-side PASS_THROUGH
#     dispatch separately, but this binder path covers the Java fallback.
#   - Everything else: writeNoException + return true (ack-only). The C-side
#     trampolines deliver the real metadata + control responses to the CT;
#     having the Binder ack-only for unmapped codes is enough to keep MtkBt
#     happy.
#
# Descriptor handling: Y1Bridge experience shows enforceInterface is
# brittle across ROM variations (descriptor strings drift), so we skip the
# leading strictModePolicy int32 + descriptor string and dispatch purely by
# transact code. Codes 1..37 are IBTAvrcpMusic; all others ack-only.


# direct methods

.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/os/Binder;-><init>()V

    return-void
.end method


# virtual methods

.method protected onTransact(ILandroid/os/Parcel;Landroid/os/Parcel;I)Z
    .locals 4

    # p1 = code, p2 = data, p3 = reply, p4 = flags

    # INTERFACE_TRANSACTION (0x5f4e5446) → delegate to super for queryLocalInterface etc.
    const v0, 0x5f4e5446

    if-ne p1, v0, :not_iface

    invoke-super {p0, p1, p2, p3, p4}, Landroid/os/Binder;->onTransact(ILandroid/os/Parcel;Landroid/os/Parcel;I)Z

    move-result v0

    return v0

    :not_iface
    :try_start
    # Skip strictModePolicy (int32) + descriptor (UTF-16 string with int32 char count).
    # This mirrors Y1Bridge's defensive descriptor-skip: enforceInterface
    # has been observed to mismatch across ROM variations of MtkBt and abort
    # registerCallback (code 1), which leaves cardinality empty forever.
    invoke-virtual {p2}, Landroid/os/Parcel;->readInt()I

    invoke-virtual {p2}, Landroid/os/Parcel;->readString()Ljava/lang/String;

    # Dispatch by code. Codes 1..37 handled here; everything else ack-only.
    packed-switch p1, :pswitch_data

    # Default: ack-only.
    goto :ack_only

    :pswitch_data
    .packed-switch 0x1
        :case_1_register
        :case_2_unregister
        :case_3_regNotificationEvent
        :case_4_setPlayerAppSetting
        :case_5_getCapabilities
        :case_6_play
        :case_7_stop
        :case_8_pause
        :case_9_resume
        :case_10_prev
        :case_11_next
        :case_12_prevGroup
        :case_13_nextGroup
    .end packed-switch

    # --- code 1: registerCallback(IBTAvrcpMusicCallback cb) ---
    :case_1_register
    invoke-virtual {p2}, Landroid/os/Parcel;->readStrongBinder()Landroid/os/IBinder;

    move-result-object v0

    invoke-static {v0}, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->addCallback(Landroid/os/IBinder;)V

    if-eqz p3, :case_1_done

    invoke-virtual {p3}, Landroid/os/Parcel;->writeNoException()V

    :case_1_done
    const-string v1, "Y1Patch"

    const-string v2, "AvrcpBinder.registerCallback"

    invoke-static {v1, v2}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I

    goto/16 :done_true

    # --- code 2: unregisterCallback(IBTAvrcpMusicCallback cb) ---
    :case_2_unregister
    invoke-virtual {p2}, Landroid/os/Parcel;->readStrongBinder()Landroid/os/IBinder;

    move-result-object v0

    invoke-static {v0}, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->removeCallback(Landroid/os/IBinder;)V

    if-eqz p3, :done_true

    invoke-virtual {p3}, Landroid/os/Parcel;->writeNoException()V

    goto/16 :done_true

    # --- code 3: regNotificationEvent(byte, int) -> boolean ---
    # Critical: must ack true. Returning false leaves MtkBt's mRegBit empty
    # and every later notifyTrackChanged gets dropped pre-emit.
    :case_3_regNotificationEvent
    # Skip the 2 inbound args so the parcel cursor advances cleanly.
    invoke-virtual {p2}, Landroid/os/Parcel;->readByte()B

    invoke-virtual {p2}, Landroid/os/Parcel;->readInt()I

    if-eqz p3, :done_true

    invoke-virtual {p3}, Landroid/os/Parcel;->writeNoException()V

    const/4 v0, 0x1

    invoke-virtual {p3, v0}, Landroid/os/Parcel;->writeInt(I)V

    goto/16 :done_true

    # --- code 4: setPlayerApplicationSettingValue(byte, byte) -> boolean ---
    :case_4_setPlayerAppSetting
    invoke-virtual {p2}, Landroid/os/Parcel;->readByte()B

    invoke-virtual {p2}, Landroid/os/Parcel;->readByte()B

    if-eqz p3, :done_true

    invoke-virtual {p3}, Landroid/os/Parcel;->writeNoException()V

    const/4 v0, 0x1

    invoke-virtual {p3, v0}, Landroid/os/Parcel;->writeInt(I)V

    goto/16 :done_true

    # --- code 5: getCapabilities() -> byte[] ---
    # Advertise the two AVRCP §5.4.2 events MtkBt's adapter notifies on
    # (PLAYBACK_STATUS_CHANGED + TRACK_CHANGED). Some 1.3 CTs gate their
    # REGISTER_NOTIFICATION emission on a non-empty capabilities byte[].
    :case_5_getCapabilities
    if-eqz p3, :done_true

    invoke-virtual {p3}, Landroid/os/Parcel;->writeNoException()V

    const/4 v0, 0x2

    new-array v0, v0, [B

    const/4 v1, 0x0

    const/4 v2, 0x1

    aput-byte v2, v0, v1

    const/4 v2, 0x2

    aput-byte v2, v0, v2

    invoke-virtual {p3, v0}, Landroid/os/Parcel;->writeByteArray([B)V

    goto/16 :done_true

    # --- code 6: play → KEYCODE_MEDIA_PLAY_PAUSE (85, toggle path) ---
    :case_6_play
    const/16 v0, 0x55

    invoke-static {v0}, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sendMediaKey(I)V

    goto/16 :ack_only

    # --- code 7: stop → KEYCODE_MEDIA_STOP (86) ---
    :case_7_stop
    const/16 v0, 0x56

    invoke-static {v0}, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sendMediaKey(I)V

    goto/16 :ack_only

    # --- code 8: pause → KEYCODE_MEDIA_PLAY_PAUSE (85, toggle path) ---
    :case_8_pause
    const/16 v0, 0x55

    invoke-static {v0}, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sendMediaKey(I)V

    goto/16 :ack_only

    # --- code 9: resume → KEYCODE_MEDIA_PLAY_PAUSE (85, toggle path) ---
    :case_9_resume
    const/16 v0, 0x55

    invoke-static {v0}, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sendMediaKey(I)V

    goto/16 :ack_only

    # --- code 10: prev → KEYCODE_MEDIA_PREVIOUS (88) ---
    :case_10_prev
    const/16 v0, 0x58

    invoke-static {v0}, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sendMediaKey(I)V

    goto/16 :ack_only

    # --- code 11: next → KEYCODE_MEDIA_NEXT (87) ---
    :case_11_next
    const/16 v0, 0x57

    invoke-static {v0}, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sendMediaKey(I)V

    goto/16 :ack_only

    # --- code 12: prevGroup → KEYCODE_MEDIA_PREVIOUS (88) ---
    :case_12_prevGroup
    const/16 v0, 0x58

    invoke-static {v0}, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sendMediaKey(I)V

    goto/16 :ack_only

    # --- code 13: nextGroup → KEYCODE_MEDIA_NEXT (87) ---
    :case_13_nextGroup
    const/16 v0, 0x57

    invoke-static {v0}, Lcom/koensayr/y1/avrcp/AvrcpBridgeService;->sendMediaKey(I)V

    goto :ack_only

    # --- ack-only catch-all ---
    # Every other AIDL method (setEqualize/Shuffle/Repeat/Scan, get*, inform*,
    # enqueue / open / getNowPlaying / getNowPlayingItemName / setQueuePosition,
    # plus the IMediaPlaybackService codes) returns writeNoException + true.
    # The trampoline chain has already responded with the real data on the
    # AVRCP wire; the Java return-value isn't read by MtkBt's hot path.
    :ack_only
    if-eqz p3, :done_true

    invoke-virtual {p3}, Landroid/os/Parcel;->writeNoException()V

    :done_true
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch

    const/4 v0, 0x1

    return v0

    :catch
    move-exception v0

    const-string v1, "Y1Patch"

    new-instance v2, Ljava/lang/StringBuilder;

    invoke-direct {v2}, Ljava/lang/StringBuilder;-><init>()V

    const-string v3, "AvrcpBinder.onTransact code="

    invoke-virtual {v2, v3}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v2, p1}, Ljava/lang/StringBuilder;->append(I)Ljava/lang/StringBuilder;

    const-string v3, ": "

    invoke-virtual {v2, v3}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v0}, Ljava/lang/Throwable;->toString()Ljava/lang/String;

    move-result-object v3

    invoke-virtual {v2, v3}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v2}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v2

    invoke-static {v1, v2}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I

    const/4 v0, 0x1

    return v0
.end method
