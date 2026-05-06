package com.y1.mediabridge;

import android.app.PendingIntent;
import android.app.Service;
import android.content.ComponentName;
import android.content.ContentUris;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.AudioManager;
import android.media.MediaMetadataRetriever;
import android.media.MediaScannerConnection;
import android.media.RemoteControlClient;
import android.net.Uri;
import android.os.Binder;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Parcel;
import android.os.RemoteException;
import android.os.SystemClock;
import android.provider.MediaStore;
import android.util.Log;
import android.view.KeyEvent;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.UnsupportedEncodingException;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * MediaBridgeService — single-process AVRCP metadata bridge for the Innioasis Y1.
 *
 * WHY THIS EXISTS
 * ===============
 * Y1 stock player plays audio but does not publish metadata on the MtkBt AVRCP
 * path. MtkBt binds to "com.android.music.MediaPlaybackService" for metadata
 * and wraps the returned IBinder with two different AIDL proxies.
 *
 *   1. IBTAvrcpMusic  (descriptor com.mediatek.bluetooth.avrcp.IBTAvrcpMusic)
 *      Ground-truth transaction codes extracted from MtkBt.odex → MtkBt.dex,
 *      class com.mediatek.bluetooth.avrcp.IBTAvrcpMusic$Stub:
 *          1  registerCallback(IBTAvrcpMusicCallback)
 *          2  unregisterCallback(IBTAvrcpMusicCallback)
 *          3  regNotificationEvent(byte,int)       -> boolean
 *          4  setPlayerApplicationSettingValue(b,b)-> boolean
 *          5  getCapabilities()                    -> byte[]
 *          6  play()                               -> void
 *          7  stop()                               -> void
 *          8  pause()                              -> void
 *          9  resume()                             -> void
 *         10  prev()                               -> void
 *         11  next()                               -> void
 *         12  prevGroup()                          -> void
 *         13  nextGroup()                          -> void
 *         14  setEqualizeMode(int)                 -> boolean
 *         15  getEqualizeMode()                    -> int
 *         16  setShuffleMode(int)                  -> boolean
 *         17  getShuffleMode()                     -> int
 *         18  setRepeatMode(int)                   -> boolean
 *         19  getRepeatMode()                      -> int
 *         20  setScanMode(int)                     -> boolean
 *         21  getScanMode()                        -> int
 *         22  informDisplayableCharacterSet(int)   -> boolean
 *         23  informBatteryStatusOfCT()            -> boolean
 *         24  getPlayStatus()                      -> byte
 *         25  position()                           -> long
 *         26  duration()                           -> long
 *         27  getAudioId()                         -> long
 *         28  getTrackName()                       -> String
 *         29  getAlbumName()                       -> String
 *         30  getAlbumId()                         -> long
 *         31  getArtistName()                      -> String
 *         32  enqueue(long[], int)                 -> void
 *         33  getNowPlaying()                      -> long[]
 *         34  getNowPlayingItemName(long)          -> String
 *         35  open(long[], int)                    -> void
 *         36  getQueuePosition()                   -> int
 *         37  setQueuePosition(int)                -> void
 *
 *   2. IMediaPlaybackService  (descriptor com.android.music.IMediaPlaybackService)
 *      Ground truth from MtkBt.dex IMediaPlaybackService$Stub. MtkBt only reads
 *      these on its metadata path:
 *          4=isPlaying, 13=getTrackName, 14=getAlbumName, 16=getArtistName,
 *         24=getAudioId, 11=position, 10=duration, 15=getAlbumId, 17=getArtistId.
 *      (Setters and queue ops exist on the interface but MtkBt never calls them.)
 *
 * Both proxies call transact() on the SAME IBinder. The transaction codes
 * overlap by number but mean different things per interface (e.g. code 6 is
 * "pause" on IMediaPlaybackService but "play" on IBTAvrcpMusic). Therefore
 * onTransact dispatches on the interface token at the head of the Parcel,
 * not on the code alone.
 *
 * WHY EVERY DECLARED CODE NEEDS A WELL-FORMED REPLY
 * =================================================
 * Returning false from onTransact tells the kernel binder driver the code is
 * unknown; the caller's generated Stub.Proxy then throws RemoteException or
 * reads a malformed reply. On the MtkBt side this makes BTAvrcpMusicAdapter
 * swallow errors silently — in particular, if regNotificationEvent (code 3)
 * fails, mRegBit never populates and every subsequent notifyTrackChanged the
 * callback fires is dropped before the AVRCP packet is emitted. That is the
 * "[BT][AVRCP] onReceive EVENT_TRACK_CHANGED fail" symptom. So every declared
 * code returns true and writes writeNoException() plus a typed zero/empty/true
 * for the declared return type, even when we have no semantic answer.
 *
 * IBTAvrcpMusicCallback (outgoing calls MtkBt subscribes to via registerCallback).
 * Ground truth from IBTAvrcpMusicCallback$Stub:
 *    1=notifyPlaybackStatus(byte)  (1=stopped,2=playing,3=paused)
 *    2=notifyTrackChanged(long)
 *    3=notifyTrackReachStart
 *    4=notifyTrackReachEnd
 *    5=notifyPlaybackPosChanged
 *    6=notifyAppSettingChanged
 *    7=notifyNowPlayingContentChanged
 *    8=notifyVolumehanged(byte)
 *
 * ARCHITECTURE
 * ============
 * Installed at /system/app/Y1MediaBridge.apk, android:persistent="true" so
 * this survives low-memory kills. A background thread reads logcat directly
 * (READ_LOGS permission, granted to /system/app on Android 4.2) and parses
 * the Y1 player's debug lines to keep the track/state fields current. When
 * anything changes we (a) write RCC metadata and (b) fire the callbacks MtkBt
 * registered with us, which is what makes the car head unit update.
 *
 * NO AUDIO FOCUS
 * ==============
 * We deliberately do NOT call requestAudioFocus(). AUDIOFOCUS_GAIN would make
 * any focus-respecting player (including possibly the stock Y1 player) pause.
 * MtkBt queries us through the direct Binder above, so audio focus is not on
 * the critical path for metadata. RCC still registers for lockscreen use.
 */
public class MediaBridgeService extends Service {

    private static final String TAG = "Y1MediaBridge";

    public static final String ACTION_PLAY_SONG = "com.y1.mediabridge.PLAY_SONG";
    public static final String ACTION_SHUTDOWN  = "com.y1.mediabridge.SHUTDOWN";

    /** Max album-art dimension; larger bitmaps blow binder transaction size. */
    private static final int MAX_ART_PX = 512;

    private static final String DESCRIPTOR_AVRCP_MUSIC =
            "com.mediatek.bluetooth.avrcp.IBTAvrcpMusic";
    private static final String DESCRIPTOR_AVRCP_CALLBACK =
            "com.mediatek.bluetooth.avrcp.IBTAvrcpMusicCallback";
    private static final String DESCRIPTOR_MEDIA_PLAYBACK =
            "com.android.music.IMediaPlaybackService";

    // -----------------------------------------------------------------------
    // Y1 logcat patterns. BasePlayerActivity is a strict superstring of
    // BaseActivity — we test the longer tag first to avoid false matches.
    //
    //   D/DebugY1  BasePlayerActivity(...): 刷新一次歌词 /storage/sdcard0/Music/...
    //   D/DebugY1  BasePlayerActivity(...): 刷新一次专辑图 /storage/sdcard0/Music/...
    //   I/DebugY1  BaseActivity(...):       播放状态切换   1    (playing)
    //   I/DebugY1  BaseActivity(...):       播放状态切换   3    (paused)
    // -----------------------------------------------------------------------

    private static final String TAG_BASE_PLAYER   = "DebugY1  BasePlayerActivity";
    private static final String TAG_BASE_ACTIVITY = "DebugY1  BaseActivity";
    private static final String PREFIX_LYRICS     = "刷新一次歌词 ";
    private static final String PREFIX_ALBUM      = "刷新一次专辑图 ";
    private static final String PREFIX_STATE      = "播放状态切换";

    // -----------------------------------------------------------------------
    // Current track state. volatile because the Binder thread reads, the
    // main thread (and the logcat monitor via mMainHandler) writes.
    // -----------------------------------------------------------------------

    private volatile String  mCurrentPath     = "";
    private volatile String  mCurrentTitle    = "";
    private volatile String  mCurrentArtist   = "";
    private volatile String  mCurrentAlbum    = "";
    private volatile long    mCurrentDuration = 0;
    private volatile long    mCurrentAudioId  = -1;
    private volatile long    mCurrentAlbumId  = -1;
    private volatile long    mCurrentArtistId = -1;
    private volatile boolean mIsPlaying       = false;

    /** Position at last state change; with mStateChangeTime gives us a live
     *  running position estimate since the stock player never reports one. */
    private volatile long mPositionAtStateChange = 0;
    private volatile long mStateChangeTime       = 0;

    private Bitmap mCurrentAlbumArt;

    private AudioManager        mAudioManager;
    private RemoteControlClient mRemoteControlClient;
    private ComponentName       mMediaButtonReceiver;

    private final Handler mMainHandler = new Handler(Looper.getMainLooper());
    private LogcatMonitor mLogcatMonitor;

    /** Path currently being scanned — prevents duplicate MediaScanner requests
     *  when the player emits both a lyrics line and an album-art line for the
     *  same track before the first scan completes. Written/read on main thread. */
    private String mPendingScanPath = null;

    /** Callback IBinders registered by MtkBt via IBTAvrcpMusic.registerCallback. */
    private final CopyOnWriteArrayList<IBinder> mAvrcpCallbacks =
            new CopyOnWriteArrayList<IBinder>();

    // =======================================================================
    // The binder — single instance, dual-interface dispatch (see AvrcpBinder)
    // =======================================================================

    /**
     * The binder — single instance, dual-interface dispatch.
     *
     * Plain Binder subclass — deliberately does NOT call attachInterface and
     * does NOT implement IInterface. This ensures IBTAvrcpMusic.Stub.asInterface()
     * always takes the remote Proxy path: queryLocalInterface returns null →
     * asInterface wraps us in a Proxy → all calls including registerCallback
     * arrive as binder transactions at onTransact where our handlers live.
     *
     * History: attachInterface was added (versionCode 7) hoping to populate
     * queryLocalInterface so asInterface() takes the local path. It did — but
     * the local path casts the result to IBTAvrcpMusic, which AvrcpBinder does
     * not implement. MtkBt's registerCallback call hit a missing method and was
     * silently swallowed, leaving callbacks=0 permanently (confirmed by
     * notifyPlaybackStatus callbacks=0 throughout the versionCode 7 session).
     * Removing attachInterface forces the Proxy path for all calls.
     */
    private final class AvrcpBinder extends Binder {
        AvrcpBinder() {
            // No attachInterface — see class javadoc.
        }

        @Override
        public String getInterfaceDescriptor() {
            return DESCRIPTOR_AVRCP_MUSIC;
        }

        @Override
        protected boolean onTransact(int code, Parcel data, Parcel reply, int flags)
                throws RemoteException {
            // Log.e so this is visible regardless of log-level filtering.
            Log.e(TAG, "onTransact: code=" + code + " pid=" + getCallingPid()
                    + " uid=" + getCallingUid());
            if (code == INTERFACE_TRANSACTION) {
                return super.onTransact(code, data, reply, flags);
            }

            // Skip the interface token header unconditionally rather than
            // enforcing it. Every previous dispatch strategy (peek+rewind,
            // try/enforceInterface/catch) failed because the descriptor string
            // MtkBt writes may differ from what we expect — different package
            // path, ROM variation, or encoding difference — causing all
            // transactions including registerCallback (code 1) to fall through
            // to super.onTransact() and return false, silently aborting
            // registration and leaving cardinality permanently at 0.
            //
            // writeInterfaceToken writes:
            //   int32  strictModePolicy
            //   string descriptor (int32 charCount + UTF-16 chars, 4-byte aligned)
            // We consume both to advance the cursor to the first argument byte,
            // then dispatch on code number alone. Both interfaces share this
            // layout so the skip is correct for either caller.
            data.readInt();    // strictModePolicy — discard
            data.readString(); // descriptor — discard

            // IBTAvrcpMusic covers codes 1–37. IMediaPlaybackService codes
            // (4, 10–17, 24) all fall within that range and are handled there
            // with compatible return types, so route everything 1–37 to the
            // AVRCP handler. Anything outside that range falls to the media
            // playback handler (defensive; MtkBt shouldn't send those here).
            if (code >= 1 && code <= 37) {
                return handleAvrcpMusic(code, data, reply);
            }
            return handleMediaPlayback(code, data, reply);
        }
    }

    private final AvrcpBinder mBinder = new AvrcpBinder();

    // -----------------------------------------------------------------------
    // IBTAvrcpMusic dispatch — every declared code returns a well-formed reply
    // even when we have no semantic answer, because returning false / garbage
    // makes MtkBt's BTAvrcpMusicAdapter abort registration (see class doc).
    // -----------------------------------------------------------------------

    private boolean handleAvrcpMusic(int code, Parcel data, Parcel reply)
            throws RemoteException {
        switch (code) {
            case 1: { // registerCallback(IBTAvrcpMusicCallback cb)
                // On the local binder path this is called directly by
                // BTAvrcpMusicAdapter.onServiceConnected — no onTransact involved.
                // On the remote path it arrives via onTransact code 1.
                IBinder cb = data.readStrongBinder();
                Log.d(TAG, "IBTAvrcpMusic.registerCallback cb=" + cb
                        + " pid=" + android.os.Binder.getCallingPid()
                        + " uid=" + android.os.Binder.getCallingUid());
                if (cb != null && !mAvrcpCallbacks.contains(cb)) {
                    mAvrcpCallbacks.add(cb);
                    Log.d(TAG, "IBTAvrcpMusic.registerCallback registered total="
                            + mAvrcpCallbacks.size()
                            + " — pushing status=" + (mIsPlaying ? 2 : 3)
                            + " audioId=" + mCurrentAudioId);
                    notifyAvrcpCallbacks(1, mIsPlaying ? (byte) 2 : (byte) 3);
                    notifyAvrcpCallbacks(2, mCurrentAudioId);
                } else {
                    Log.w(TAG, "IBTAvrcpMusic.registerCallback: cb="
                            + (cb == null ? "null" : "duplicate"));
                }
                if (reply != null) reply.writeNoException();
                return true;
            }
            case 2: { // unregisterCallback(IBTAvrcpMusicCallback cb)
                IBinder cb = data.readStrongBinder();
                boolean removed = cb != null && mAvrcpCallbacks.remove(cb);
                Log.d(TAG, "IBTAvrcpMusic.unregisterCallback cb=" + cb
                        + " removed=" + removed + " remaining=" + mAvrcpCallbacks.size());
                if (reply != null) reply.writeNoException();
                return true;
            }

            case 3: { // regNotificationEvent(byte eventId, int param) -> boolean
                // CRITICAL: Called by BTAvrcpMusicAdapter.registerNotification
                // when the car subscribes to EVENT_TRACK_CHANGED (0x05),
                // EVENT_PLAYBACK_STATUS_CHANGED (0x01), etc. Returning false
                // leaves MtkBt's mRegBit empty and every later notifyTrackChanged
                // gets dropped before the AVRCP packet is emitted. We always
                // ack success (true). The adapter owns the bitset, not us.
                byte eventId = data.readByte();
                int  param   = data.readInt();
                Log.d(TAG, "IBTAvrcpMusic.regNotificationEvent event=0x"
                        + Integer.toHexString(eventId & 0xff) + " param=" + param);
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;
            }

            case 4: { // setPlayerApplicationSettingValue(byte attr, byte val) -> boolean
                // Read args so the parcel is consumed; we don't apply them.
                try { data.readByte(); data.readByte(); } catch (Exception ignored) {}
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;
            }

            case 5: { // getCapabilities() -> byte[]
                // MtkBt stashes this in mCapabilities and the car reads it
                // during GET_CAPABILITIES to decide which events to register
                // for via REGISTER_NOTIFICATION. An empty array is legal per
                // our AIDL contract, but some AVRCP 1.3 CTs interpret it as
                // "TG supports no events" and skip REGISTER_NOTIFICATION
                // entirely — leaving cardinality:0 and no metadata flow.
                //
                // Return the two mandatory AVRCP event IDs:
                //   0x01 = EVENT_PLAYBACK_STATUS_CHANGED
                //   0x02 = EVENT_TRACK_CHANGED
                // These are the only events MtkBt's BTAvrcpMusicAdapter
                // notifies us about (via notifyPlaybackStatus and
                // notifyTrackChanged), so this is both necessary and sufficient.
                Log.d(TAG, "IBTAvrcpMusic.getCapabilities called → returning [0x01, 0x02]"
                        + " pid=" + android.os.Binder.getCallingPid());
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeByteArray(new byte[]{ 0x01, 0x02 });
                }
                return true;
            }

            // Transport commands — forward as media keys to the stock player.
            // Note: IBTAvrcpMusic code 6 = play, NOT pause (differs from
            // IMediaPlaybackService which uses 6=pause, 7=play).
            case 6:  Log.d(TAG, "IBTAvrcpMusic.play → KEYCODE_MEDIA_PLAY");   return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PLAY);
            case 7:  Log.d(TAG, "IBTAvrcpMusic.stop → KEYCODE_MEDIA_STOP");   return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_STOP);
            case 8:  Log.d(TAG, "IBTAvrcpMusic.pause → KEYCODE_MEDIA_PAUSE"); return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PAUSE);
            case 9:  Log.d(TAG, "IBTAvrcpMusic.resume → KEYCODE_MEDIA_PLAY"); return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PLAY);
            case 10: Log.d(TAG, "IBTAvrcpMusic.prev → KEYCODE_MEDIA_PREVIOUS"); return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PREVIOUS);
            case 11: Log.d(TAG, "IBTAvrcpMusic.next → KEYCODE_MEDIA_NEXT");   return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_NEXT);
            case 12: Log.d(TAG, "IBTAvrcpMusic.prevGroup → KEYCODE_MEDIA_PREVIOUS"); return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PREVIOUS);
            case 13: Log.d(TAG, "IBTAvrcpMusic.nextGroup → KEYCODE_MEDIA_NEXT");     return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_NEXT);

            // Setter/getter pairs for player-app settings. Setters return
            // boolean success; getters return int (zero = not-applicable).
            case 14: // setEqualizeMode(int) -> boolean
            case 16: // setShuffleMode(int)  -> boolean
            case 18: // setRepeatMode(int)   -> boolean
            case 20: // setScanMode(int)     -> boolean
                try { data.readInt(); } catch (Exception ignored) {}
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;
            case 15: // getEqualizeMode()  -> int
            case 17: // getShuffleMode()   -> int
            case 19: // getRepeatMode()    -> int
            case 21: // getScanMode()      -> int
            case 36: // getQueuePosition() -> int
                if (reply != null) { reply.writeNoException(); reply.writeInt(0); }
                return true;

            case 22: // informDisplayableCharacterSet(int) -> boolean
            case 23: // informBatteryStatusOfCT()         -> boolean
                if (code == 22) { try { data.readInt(); } catch (Exception ignored) {} }
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;

            case 24: // getPlayStatus() -> byte (1=stopped, 2=playing, 3=paused)
                if (reply != null) {
                    byte status = mIsPlaying ? (byte) 2 : (byte) 3;
                    Log.v(TAG, "IBTAvrcpMusic.getPlayStatus → " + status);
                    reply.writeNoException();
                    reply.writeByte(status);
                }
                return true;
            case 25: // position() -> long
                if (reply != null) {
                    long pos = computePosition();
                    Log.v(TAG, "IBTAvrcpMusic.position → " + pos);
                    reply.writeNoException(); reply.writeLong(pos);
                }
                return true;
            case 26: // duration() -> long
                if (reply != null) {
                    Log.v(TAG, "IBTAvrcpMusic.duration → " + mCurrentDuration);
                    reply.writeNoException(); reply.writeLong(mCurrentDuration);
                }
                return true;
            case 27: // getAudioId() -> long
                if (reply != null) {
                    Log.v(TAG, "IBTAvrcpMusic.getAudioId → " + mCurrentAudioId);
                    reply.writeNoException(); reply.writeLong(mCurrentAudioId);
                }
                return true;
            case 28: // getTrackName() -> String
                if (reply != null) {
                    Log.v(TAG, "IBTAvrcpMusic.getTrackName → " + safeString(mCurrentTitle));
                    reply.writeNoException(); reply.writeString(safeString(mCurrentTitle));
                }
                return true;
            case 29: // getAlbumName() -> String
                if (reply != null) {
                    Log.v(TAG, "IBTAvrcpMusic.getAlbumName → " + safeString(mCurrentAlbum));
                    reply.writeNoException(); reply.writeString(safeString(mCurrentAlbum));
                }
                return true;
            case 30: // getAlbumId() -> long (IBTAvrcpMusic numbering)
                if (reply != null) {
                    Log.v(TAG, "IBTAvrcpMusic.getAlbumId → " + mCurrentAlbumId);
                    reply.writeNoException(); reply.writeLong(mCurrentAlbumId);
                }
                return true;
            case 31: // getArtistName() -> String
                if (reply != null) {
                    Log.v(TAG, "IBTAvrcpMusic.getArtistName → " + safeString(mCurrentArtist));
                    reply.writeNoException(); reply.writeString(safeString(mCurrentArtist));
                }
                return true;

            case 32: // enqueue(long[], int) -> void
            case 35: // open(long[], int)    -> void
                try { data.createLongArray(); data.readInt(); } catch (Exception ignored) {}
                if (reply != null) reply.writeNoException();
                return true;

            case 33: // getNowPlaying() -> long[]
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeLongArray(new long[0]);
                }
                return true;

            case 34: // getNowPlayingItemName(long) -> String
                try { data.readLong(); } catch (Exception ignored) {}
                if (reply != null) { reply.writeNoException(); reply.writeString(""); }
                return true;

            case 37: // setQueuePosition(int) -> void
                try { data.readInt(); } catch (Exception ignored) {}
                if (reply != null) reply.writeNoException();
                return true;

            default:
                // Unknown codes — delegate to super so INTERFACE_TRANSACTION
                // and DUMP / PING etc. still work.
                Log.v(TAG, "IBTAvrcpMusic: unhandled code=" + code
                        + " — falling through to super");
                return false;
        }
    }

    private boolean avrcpAck(Parcel data, Parcel reply, int keyCode)
            throws RemoteException {
        // All void transport methods still must consume the interface token
        // and write writeNoException so the caller's readException sees a
        // clean reply parcel.
        sendMediaKey(keyCode);
        if (reply != null) reply.writeNoException();
        return true;
    }

    // -----------------------------------------------------------------------
    // IMediaPlaybackService dispatch (AOSP-style interface as packaged inside
    // MtkBt.dex — codes verified against com.android.music.IMediaPlaybackService$Stub).
    //
    // MtkBt's BTAvrcpMusicAdapter also wraps our binder with
    // IMediaPlaybackService.Stub.asInterface() for the metadata read path.
    // Earlier hand-derived code tables had a few codes off — corrected here
    // from the authoritative TRANSACTION_* fields extracted from the DEX.
    // -----------------------------------------------------------------------

    private boolean handleMediaPlayback(int code, Parcel data, Parcel reply)
            throws RemoteException {
        switch (code) {
            case 4:  // isPlaying() -> int (0/1)
                Log.v(TAG, "IMediaPlayback.isPlaying → " + mIsPlaying);
                if (reply != null) { reply.writeNoException(); reply.writeInt(mIsPlaying ? 1 : 0); }
                return true;
            case 10: // duration() -> long
                Log.v(TAG, "IMediaPlayback.duration → " + mCurrentDuration);
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentDuration); }
                return true;
            case 11: // position() -> long
                Log.v(TAG, "IMediaPlayback.position → " + computePosition());
                if (reply != null) { reply.writeNoException(); reply.writeLong(computePosition()); }
                return true;
            case 13: // getTrackName() -> String
                Log.v(TAG, "IMediaPlayback.getTrackName → " + safeString(mCurrentTitle));
                if (reply != null) { reply.writeNoException(); reply.writeString(safeString(mCurrentTitle)); }
                return true;
            case 14: // getAlbumName() -> String
                Log.v(TAG, "IMediaPlayback.getAlbumName → " + safeString(mCurrentAlbum));
                if (reply != null) { reply.writeNoException(); reply.writeString(safeString(mCurrentAlbum)); }
                return true;
            case 15: // getAlbumId() -> long
                Log.v(TAG, "IMediaPlayback.getAlbumId → " + mCurrentAlbumId);
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentAlbumId); }
                return true;
            case 16: // getArtistName() -> String
                Log.v(TAG, "IMediaPlayback.getArtistName → " + safeString(mCurrentArtist));
                if (reply != null) { reply.writeNoException(); reply.writeString(safeString(mCurrentArtist)); }
                return true;
            case 17: // getArtistId() -> long
                Log.v(TAG, "IMediaPlayback.getArtistId → " + mCurrentArtistId);
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentArtistId); }
                return true;
            case 24: // getAudioId() -> long
                Log.v(TAG, "IMediaPlayback.getAudioId → " + mCurrentAudioId);
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentAudioId); }
                return true;
            default:
                // MtkBt only queries the codes above on the metadata path,
                // but other codes might leak through from legacy paths. Ack
                // everything else as a no-op void so the RPC succeeds.
                Log.v(TAG, "IMediaPlaybackService: ack-only code=" + code);
                if (reply != null) reply.writeNoException();
                return true;
        }
    }

    // -----------------------------------------------------------------------
    // Outgoing IBTAvrcpMusicCallback invocations to all registered MtkBt
    // callback binders. These are what actually make AVRCP events fire.
    // -----------------------------------------------------------------------

    private void notifyAvrcpCallbacks(int code, Object... args) {
        if (mAvrcpCallbacks.isEmpty()) {
            Log.v(TAG, "notifyAvrcpCallbacks code=" + code + " — no callbacks registered");
            return;
        }
        // Build a readable arg summary for logging
        StringBuilder argStr = new StringBuilder();
        if (args != null) {
            for (Object a : args) argStr.append(a).append(' ');
        }
        Log.d(TAG, "notifyAvrcpCallbacks code=" + code
                + " args=[" + argStr.toString().trim() + "]"
                + " targets=" + mAvrcpCallbacks.size());
        for (IBinder cb : mAvrcpCallbacks) {
            Parcel data  = Parcel.obtain();
            Parcel reply = Parcel.obtain();
            try {
                data.writeInterfaceToken(DESCRIPTOR_AVRCP_CALLBACK);
                if (args != null) {
                    for (Object arg : args) {
                        if (arg instanceof Byte)         data.writeByte((Byte) arg);
                        else if (arg instanceof Long)    data.writeLong((Long) arg);
                        else if (arg instanceof Integer) data.writeInt((Integer) arg);
                    }
                }
                int rc = cb.transact(code, data, reply, 0) ? 0 : -1;
                Log.v(TAG, "notifyAvrcpCallbacks code=" + code + " cb=" + cb + " rc=" + rc);
            } catch (RemoteException e) {
                Log.w(TAG, "AVRCP callback transact failed code=" + code
                        + " cb=" + cb + " — dropping: " + e);
                mAvrcpCallbacks.remove(cb);
            } finally {
                reply.recycle();
                data.recycle();
            }
        }
    }

    private void notifyPlaybackStatus(byte status) {
        Log.d(TAG, "notifyPlaybackStatus status=" + status
                + " (" + (status == 2 ? "playing" : status == 3 ? "paused" : "stopped") + ")"
                + " callbacks=" + mAvrcpCallbacks.size());
        notifyAvrcpCallbacks(1, status);
    }
    private void notifyTrackChanged(long id) {
        Log.d(TAG, "notifyTrackChanged id=" + id
                + " title=" + safeString(mCurrentTitle)
                + " callbacks=" + mAvrcpCallbacks.size());
        notifyAvrcpCallbacks(2, id);
    }

    /** Live position estimate. Approximate since the Y1 player emits no
     *  position updates, but accurate enough for head-unit scrub bars. */
    private long computePosition() {
        if (!mIsPlaying) return mPositionAtStateChange;
        long elapsed = SystemClock.elapsedRealtime() - mStateChangeTime;
        long pos = mPositionAtStateChange + elapsed;
        if (mCurrentDuration > 0 && pos > mCurrentDuration) pos = mCurrentDuration;
        return pos;
    }

    // =======================================================================
    // Service lifecycle
    // =======================================================================

    @Override
    public void onCreate() {
        super.onCreate();
        Log.d(TAG, "MediaBridgeService created versionCode=11 (pid=" + android.os.Process.myPid()
                + " uid=" + android.os.Process.myUid() + ")");

        mAudioManager = (AudioManager) getSystemService(AUDIO_SERVICE);
        setupRemoteControlClient();
        startLogcatMonitor();
        prepareTrackInfoDir();
    }

    /**
     * Make our private files dir traversable by other uids so the AVRCP T4
     * trampoline (running in the Bluetooth process, uid bluetooth) can open
     * /data/data/com.y1.mediabridge/files/y1-track-info.
     *
     * Also ensure y1-trampoline-state exists, owned by us but world-rw, so
     * the BT process can stash its "last seen track_id" + "last register
     * transId" between AVRCP exchanges. See iter15 in docs/ARCHITECTURE.md.
     *
     * Default Android filesDir mode is 0700 (owner-only). We chmod to add
     * world execute (traversal). The file we write inside is then made
     * world-readable via setReadable(true, false).
     */
    private void prepareTrackInfoDir() {
        try {
            File dir = getFilesDir();
            if (dir == null) return;
            boolean ok = dir.setExecutable(true, false);
            Log.d(TAG, "prepareTrackInfoDir: setExecutable on " + dir.getPath()
                    + " → " + ok);

            // Create y1-trampoline-state if missing. 16 zero bytes:
            //   bytes 0..7  = last_seen_track_id (0 forces a CHANGED on first
            //                 GetElementAttributes, which Sonos drops as bogus
            //                 if transId is also 0 — but real subscriptions
            //                 update transId before T4 ever fires)
            //   byte  8     = last RegisterNotification transId
            //   bytes 9..15 = padding
            File state = new File(dir, TRAMPOLINE_STATE_FILENAME);
            if (!state.exists()) {
                FileOutputStream fos = new FileOutputStream(state);
                try { fos.write(new byte[STATE_LEN]); } finally { fos.close(); }
            }
            // World rw: BT process (uid bluetooth) must read AND write.
            state.setReadable(true, false);
            state.setWritable(true, false);
        } catch (Throwable t) {
            Log.w(TAG, "prepareTrackInfoDir: " + t);
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        Log.d(TAG, "onBind: " + (intent != null ? intent.getAction() : "null"));
        return mBinder;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null) {
            String action = intent.getAction();
            Log.d(TAG, "onStartCommand: " + action);
            if (ACTION_SHUTDOWN.equals(action)) {
                mIsPlaying = false;
                mStateChangeTime = SystemClock.elapsedRealtime();
                publishState();
                sendMusicBroadcast("com.android.music.playstatechanged");
                notifyPlaybackStatus((byte) 3);
            }
            // ACTION_PLAY_SONG: logcat monitor handles the actual state change.
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        Log.d(TAG, "MediaBridgeService destroyed");
        if (mLogcatMonitor != null) mLogcatMonitor.shutdown();
        if (mRemoteControlClient != null) {
            mAudioManager.unregisterRemoteControlClient(mRemoteControlClient);
        }
        if (mMediaButtonReceiver != null) {
            mAudioManager.unregisterMediaButtonEventReceiver(mMediaButtonReceiver);
        }
        if (mCurrentAlbumArt != null && !mCurrentAlbumArt.isRecycled()) {
            mCurrentAlbumArt.recycle();
        }
        super.onDestroy();
    }

    // =======================================================================
    // RemoteControlClient — not on MtkBt's critical path, but we keep it
    // registered for lockscreen / system UI. NO audio focus is requested
    // because AUDIOFOCUS_GAIN would tell any focus-respecting player
    // (possibly including the stock Y1 player) to pause.
    // =======================================================================

    private void setupRemoteControlClient() {
        mMediaButtonReceiver = new ComponentName(getPackageName(),
                PlaySongReceiver.class.getName());
        mAudioManager.registerMediaButtonEventReceiver(mMediaButtonReceiver);

        Intent mediaButtonIntent = new Intent(Intent.ACTION_MEDIA_BUTTON);
        mediaButtonIntent.setComponent(mMediaButtonReceiver);
        PendingIntent mediaPendingIntent = PendingIntent.getBroadcast(
                this, 0, mediaButtonIntent, 0);

        mRemoteControlClient = new RemoteControlClient(mediaPendingIntent);
        mRemoteControlClient.setTransportControlFlags(
                RemoteControlClient.FLAG_KEY_MEDIA_PLAY
              | RemoteControlClient.FLAG_KEY_MEDIA_PAUSE
              | RemoteControlClient.FLAG_KEY_MEDIA_PLAY_PAUSE
              | RemoteControlClient.FLAG_KEY_MEDIA_NEXT
              | RemoteControlClient.FLAG_KEY_MEDIA_PREVIOUS
              | RemoteControlClient.FLAG_KEY_MEDIA_STOP);
        mAudioManager.registerRemoteControlClient(mRemoteControlClient);

        publishState();
        Log.d(TAG, "RemoteControlClient registered (no audio focus request)");
    }

    private void publishMetadata() {
        if (mRemoteControlClient == null) return;
        RemoteControlClient.MetadataEditor editor =
                mRemoteControlClient.editMetadata(true);
        editor.putString(MediaMetadataRetriever.METADATA_KEY_TITLE,  safeString(mCurrentTitle));
        editor.putString(MediaMetadataRetriever.METADATA_KEY_ARTIST, safeString(mCurrentArtist));
        editor.putString(MediaMetadataRetriever.METADATA_KEY_ALBUM,  safeString(mCurrentAlbum));
        editor.putLong(MediaMetadataRetriever.METADATA_KEY_DURATION, mCurrentDuration);
        if (mCurrentAlbumArt != null) {
            // Bitmap must be immutable for binder transport in some ROMs.
            Bitmap immutable = mCurrentAlbumArt.copy(mCurrentAlbumArt.getConfig(), false);
            editor.putBitmap(RemoteControlClient.MetadataEditor.BITMAP_KEY_ARTWORK, immutable);
        }
        editor.apply();
        Log.d(TAG, "RCC metadata: " + mCurrentTitle + " / " + mCurrentArtist
                + (mCurrentAlbumArt != null ? " [art]" : ""));
    }

    private void publishState() {
        if (mRemoteControlClient == null) return;
        // Single-argument setPlaybackState(int) — the (state, pos, speed)
        // overload was added in API 18 and we target API 17.
        int state = mIsPlaying
                ? RemoteControlClient.PLAYSTATE_PLAYING
                : RemoteControlClient.PLAYSTATE_PAUSED;
        mRemoteControlClient.setPlaybackState(state);
        Log.d(TAG, "RCC state: " + (mIsPlaying ? "PLAYING" : "PAUSED"));
    }

    // =======================================================================
    // Logcat monitor — reads Y1 player debug lines directly from our process.
    // Requires READ_LOGS, granted to /system/app on Android 4.2 without a
    // platform signature.
    // =======================================================================

    private void startLogcatMonitor() {
        mLogcatMonitor = new LogcatMonitor();
        mLogcatMonitor.start();
    }

    private class LogcatMonitor extends Thread {
        private volatile boolean mRunning = true;
        private java.lang.Process mProcess;   // fully-qualified to disambiguate

        LogcatMonitor() {
            super("Y1-LogcatMonitor");
            setDaemon(true);
        }

        void shutdown() {
            mRunning = false;
            if (mProcess != null) mProcess.destroy();
            interrupt();
        }

        @Override
        public void run() {
            Log.d(TAG, "Logcat monitor thread started");
            // History pass first so we resync if the player was already
            // playing when we started. Then switch to live tail.
            processLogcat(new String[]{ "logcat", "-v", "tag", "-d" });
            Log.d(TAG, "Logcat history processed");
            while (mRunning) {
                processLogcat(new String[]{ "logcat", "-v", "tag" });
                if (mRunning) {
                    Log.w(TAG, "Logcat pipe closed, restarting in 1s");
                    try { Thread.sleep(1000); } catch (InterruptedException e) { break; }
                }
            }
            Log.d(TAG, "Logcat monitor thread exiting");
        }

        private void processLogcat(String[] args) {
            try {
                mProcess = Runtime.getRuntime().exec(args);
                BufferedReader reader = new BufferedReader(
                        new InputStreamReader(mProcess.getInputStream()));
                String line;
                while (mRunning && (line = reader.readLine()) != null) {
                    processLogLine(line);
                }
                reader.close();
                if (mProcess != null) {
                    mProcess.destroy();
                    mProcess = null;
                }
            } catch (Exception e) {
                Log.e(TAG, "Logcat read error: " + e.getMessage());
            }
        }
    }

    /** Dispatches mutations to the main thread so RCC & binder state aren't
     *  poked from the logcat reader thread. */
    private void processLogLine(String line) {
        if (line == null) return;

        // Check BasePlayerActivity first — "BaseActivity" is a substring of
        // it, so the BaseActivity match would otherwise fire on player lines.
        if (line.contains(TAG_BASE_PLAYER)) {
            int idx = line.indexOf(PREFIX_LYRICS);
            if (idx < 0) idx = line.indexOf(PREFIX_ALBUM);
            if (idx < 0) return;
            String prefix = line.startsWith(PREFIX_LYRICS, idx)
                    ? PREFIX_LYRICS : PREFIX_ALBUM;
            String path = line.substring(idx + prefix.length()).trim();
            if (path.isEmpty() || path.charAt(0) != '/') return;
            final String fPath = path;
            mMainHandler.post(new Runnable() {
                @Override public void run() { onTrackDetected(fPath); }
            });
            return;
        }

        if (line.contains(TAG_BASE_ACTIVITY)) {
            int idx = line.indexOf(PREFIX_STATE);
            if (idx < 0) return;
            int pos = idx + PREFIX_STATE.length();
            while (pos < line.length()
                    && (line.charAt(pos) == ' ' || line.charAt(pos) == '\t')) pos++;
            if (pos >= line.length()) return;
            char stateChar = line.charAt(pos);
            final boolean playing;
            if      (stateChar == '1') playing = true;
            else if (stateChar == '3') playing = false;
            else                       return;
            mMainHandler.post(new Runnable() {
                @Override public void run() { onStateDetected(playing); }
            });
        }
    }

    // =======================================================================
    // State / track change handlers — always on main thread
    // =======================================================================

    private void onStateDetected(boolean playing) {
        if (playing == mIsPlaying) return;
        Log.d(TAG, "State change: " + (playing ? "playing" : "paused")
                + " callbacks=" + mAvrcpCallbacks.size());
        mPositionAtStateChange = computePosition();
        mStateChangeTime = SystemClock.elapsedRealtime();
        mIsPlaying = playing;
        publishState();
        sendMusicBroadcast("com.android.music.playstatechanged");
        notifyPlaybackStatus(playing ? (byte) 2 : (byte) 3);
    }

    private void onTrackDetected(String path) {
        if (path.equals(mCurrentPath)) return;
        Log.d(TAG, "Track change: " + path);
        mCurrentPath = path;
        mPositionAtStateChange = 0;
        mStateChangeTime = SystemClock.elapsedRealtime();

        if (!queryMetadataFromStore(path)) {
            // MediaStore miss is the common case here — the Y1 player navigates
            // to files MediaStore hasn't indexed (or whose path encoding
            // doesn't match). Waiting on MediaScannerConnection.scanFile to
            // populate the DB takes 0.7–2s on this device; readTagsDirectly
            // (MediaMetadataRetriever) reads ID3 tags from the file head in
            // ~50ms, which is the difference between a Sonos refresh that
            // feels instant and one the user calls out as laggy.
            //
            // We still kick the scanner fire-and-forget so future plays of
            // this file hit MediaStore (canonical _ID, album-art lookup, etc.).
            // The current track is already on the wire by then — no second
            // broadcast is fired, which keeps msg=544 traffic at one CHANGED
            // per Y1 track change.
            readTagsDirectly(path);
            if (!path.equals(mPendingScanPath)) {
                Log.d(TAG, "MediaStore miss for " + path
                        + " — broadcasting direct tags + kicking async scan");
                mPendingScanPath = path;
                MediaScannerConnection.scanFile(this, new String[]{ path }, null,
                        new MediaScannerConnection.OnScanCompletedListener() {
                            @Override
                            public void onScanCompleted(String p, Uri uri) {
                                final String finalPath = p;
                                mMainHandler.post(new Runnable() {
                                    @Override public void run() {
                                        if (finalPath.equals(mPendingScanPath)) {
                                            mPendingScanPath = null;
                                        }
                                    }
                                });
                            }
                        });
            } else {
                Log.d(TAG, "MediaStore miss for " + path
                        + " — broadcasting direct tags (scan already running)");
            }
        }
        broadcastTrackAndState();
    }

    private void broadcastTrackAndState() {
        Log.d(TAG, "broadcastTrackAndState title=" + safeString(mCurrentTitle)
                + " artist=" + safeString(mCurrentArtist)
                + " audioId=" + mCurrentAudioId
                + " playing=" + mIsPlaying
                + " callbacks=" + mAvrcpCallbacks.size());
        publishMetadata();
        publishState();
        sendMusicBroadcast("com.android.music.metachanged");
        notifyTrackChanged(mCurrentAudioId);
        notifyPlaybackStatus(mIsPlaying ? (byte) 2 : (byte) 3);
        writeTrackInfoFile();
    }

    // =======================================================================
    // Track-info file for the AVRCP T4 trampoline (in libextavrcp_jni.so)
    //
    // Java-side AVRCP is a stub on this firmware (cardinality:0 — no peer
    // subscriptions tracked, getElementAttributesRspNative declared but never
    // called). To deliver metadata, --avrcp-min patches a native trampoline
    // chain into libextavrcp_jni.so that handles inbound AVRCP commands
    // directly. The T4 trampoline (PDU 0x20 GetElementAttributes) reads this
    // file via open(2) + read(2) syscall, then calls
    // btmtk_avrcp_send_get_element_attributes_rsp via PLT 0x3570 with the
    // strings as arguments. See docs/ARCHITECTURE.md in the y1-mods repo.
    //
    // Path is the app's private getFilesDir() rather than /data/local/tmp/
    // because Y1MediaBridge runs as uid 10000 (regular app uid, not system)
    // and /data/local/tmp/ is mode 0771 owner=shell — uid 10000 has no write
    // permission there. Our private files dir is owned by us; we chmod it
    // world-x at startup (prepareTrackInfoDir) and the file world-r here.
    //
    // File format (776 bytes total, fixed layout, null-padded UTF-8) — iter15:
    //   bytes 0..7     = track_id (mCurrentAudioId, big-endian) — used by
    //                    extended_T2 to answer RegisterNotification(TRACK_CHANGED)
    //                    and by T4 to detect when the track has changed since
    //                    the last CHANGED notification we sent
    //   bytes 8..263   = title  (255 UTF-8 bytes max + trailing null)
    //   bytes 264..519 = artist (same)
    //   bytes 520..775 = album  (same)
    //
    // Each string field is null-padded; we truncate to FIELD_LEN-1 (255) bytes
    // max so each 256-byte slot has at least one trailing 0x00 — the trampoline
    // calls strlen() on each slot start, and the trailing null bounds the read.
    //
    // Defensive: every code path is wrapped in try/catch(Throwable) so a write
    // failure (e.g., disk-full, EACCES, weird OS state) never crashes the
    // service. iter14 lost Y1MediaBridge to a silent crash when an EACCES
    // exception propagated past my IOException catch.
    private static final String TRACK_INFO_FILENAME = "y1-track-info";
    private static final String TRAMPOLINE_STATE_FILENAME = "y1-trampoline-state";
    private static final int TRACK_ID_LEN = 8;
    private static final int FIELD_LEN = 256;
    private static final int TITLE_OFFSET  = TRACK_ID_LEN;            // 8
    private static final int ARTIST_OFFSET = TITLE_OFFSET + FIELD_LEN; // 264
    private static final int ALBUM_OFFSET  = ARTIST_OFFSET + FIELD_LEN; // 520
    private static final int TOTAL_LEN     = ALBUM_OFFSET + FIELD_LEN; // 776
    private static final int STATE_LEN     = 16;

    private void writeTrackInfoFile() {
        try {
            byte[] buf = new byte[TOTAL_LEN];  // zero-initialized
            // 8-byte big-endian track_id at offset 0.
            long id = mCurrentAudioId;
            for (int i = 0; i < TRACK_ID_LEN; i++) {
                buf[i] = (byte) ((id >> (56 - i * 8)) & 0xFF);
            }
            putUtf8Padded(buf, TITLE_OFFSET,  FIELD_LEN, mCurrentTitle);
            putUtf8Padded(buf, ARTIST_OFFSET, FIELD_LEN, mCurrentArtist);
            putUtf8Padded(buf, ALBUM_OFFSET,  FIELD_LEN, mCurrentAlbum);

            File dir = getFilesDir();
            if (dir == null) {
                Log.w(TAG, "writeTrackInfoFile: getFilesDir() returned null");
                return;
            }
            File tmp = new File(dir, TRACK_INFO_FILENAME + ".tmp");
            File target = new File(dir, TRACK_INFO_FILENAME);

            FileOutputStream fos = new FileOutputStream(tmp);
            try { fos.write(buf); } finally { fos.close(); }

            if (!tmp.renameTo(target)) {
                Log.w(TAG, "writeTrackInfoFile: rename failed");
                tmp.delete();
                return;
            }
            // World-readable so the BT process (uid bluetooth) can open it.
            target.setReadable(true, false);
        } catch (Throwable t) {
            Log.w(TAG, "writeTrackInfoFile: " + t);
        }
    }

    private static void putUtf8Padded(byte[] dst, int off, int slot, String s) {
        if (s == null) return;
        byte[] src;
        try {
            // String form for API 17 compat; java.nio.charset.StandardCharsets is API 19+.
            src = s.getBytes("UTF-8");
        } catch (UnsupportedEncodingException e) {
            // UTF-8 is always supported by the JVM — this branch is unreachable.
            return;
        }
        // Truncate to slot-1 to guarantee at least one trailing null byte for strlen.
        int n = src.length < slot ? src.length : slot - 1;
        System.arraycopy(src, 0, dst, off, n);
    }

    // =======================================================================
    // Helpers
    // =======================================================================

    private void sendMusicBroadcast(String action) {
        Intent i = new Intent(action);
        i.putExtra("id",       mCurrentAudioId);
        i.putExtra("artist",   safeString(mCurrentArtist));
        i.putExtra("album",    safeString(mCurrentAlbum));
        i.putExtra("track",    safeString(mCurrentTitle));
        i.putExtra("playing",  mIsPlaying);
        i.putExtra("duration", mCurrentDuration);
        i.putExtra("position", computePosition());
        sendBroadcast(i);
    }

    private void sendMediaKey(int keyCode) {
        ComponentName target = new ComponentName("com.innioasis.y1",
                "com.innioasis.y1.receiver.PlayControllerReceiver");
        KeyEvent down = new KeyEvent(KeyEvent.ACTION_DOWN, keyCode);
        KeyEvent up   = new KeyEvent(KeyEvent.ACTION_UP,   keyCode);
        Intent i = new Intent(Intent.ACTION_MEDIA_BUTTON);
        i.setComponent(target);
        i.putExtra(Intent.EXTRA_KEY_EVENT, down);
        sendBroadcast(i);
        i = new Intent(Intent.ACTION_MEDIA_BUTTON);
        i.setComponent(target);
        i.putExtra(Intent.EXTRA_KEY_EVENT, up);
        sendBroadcast(i);
    }

    // =======================================================================
    // Metadata loading
    // =======================================================================

    private boolean queryMetadataFromStore(String path) {
        String[] projection = {
                MediaStore.Audio.Media.TITLE,
                MediaStore.Audio.Media.ARTIST,
                MediaStore.Audio.Media.ALBUM,
                MediaStore.Audio.Media.DURATION,
                MediaStore.Audio.Media.ALBUM_ID,
                MediaStore.Audio.Media.ARTIST_ID,
                MediaStore.Audio.Media._ID
        };
        Cursor cursor = null;
        try {
            cursor = getContentResolver().query(
                    MediaStore.Audio.Media.EXTERNAL_CONTENT_URI,
                    projection,
                    MediaStore.Audio.Media.DATA + "=?",
                    new String[]{ path }, null);
            if (cursor != null && cursor.moveToFirst()) {
                mCurrentTitle    = cursor.getString(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.TITLE));
                mCurrentArtist   = cursor.getString(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.ARTIST));
                mCurrentAlbum    = cursor.getString(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.ALBUM));
                mCurrentDuration = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.DURATION));
                mCurrentAlbumId  = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.ALBUM_ID));
                mCurrentArtistId = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.ARTIST_ID));
                mCurrentAudioId  = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.Audio.Media._ID));
                if ("<unknown>".equals(mCurrentArtist)) mCurrentArtist = "";
                if ("<unknown>".equals(mCurrentAlbum))  mCurrentAlbum  = "";
                mCurrentAlbumArt = loadAlbumArt(mCurrentAlbumId);
                Log.d(TAG, "MediaStore hit: " + mCurrentTitle + " / " + mCurrentArtist);
                return true;
            }
        } catch (Exception e) {
            Log.e(TAG, "MediaStore error: " + e.getMessage());
        } finally {
            if (cursor != null) cursor.close();
        }
        return false;
    }

    private void readTagsDirectly(String path) {
        MediaMetadataRetriever r = new MediaMetadataRetriever();
        FileInputStream fis = null;
        try {
            // setDataSource(String) IPCs the path to the mediaserver process
            // (uid 1013), which on this Y1 firmware can't open
            // /storage/sdcard0/Music/... — the resulting RuntimeException
            // arrives here with a null message and every miss-path track
            // was falling through to filename-only display (iter18a hardware
            // capture, "MediaMetadataRetriever error: null" on every miss).
            //
            // Open the file in our own process (uid 10000, sdcard_r group
            // grants read on /storage/sdcard0/) and pass the FileDescriptor:
            // mediaserver receives the dup'd FD over binder and reads from
            // it directly, no re-open and no permission check on its side.
            fis = new FileInputStream(path);
            r.setDataSource(fis.getFD());
            mCurrentTitle    = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_TITLE);
            mCurrentArtist   = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ARTIST);
            mCurrentAlbum    = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ALBUM);
            String dur       = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION);
            mCurrentDuration = dur != null ? Long.parseLong(dur) : 0;
            mCurrentAudioId  = -1;
            mCurrentAlbumId  = -1;
            mCurrentArtistId = -1;
            byte[] art = r.getEmbeddedPicture();
            mCurrentAlbumArt = art != null ? decodeSampled(art, MAX_ART_PX) : null;
            if (mCurrentTitle == null || mCurrentTitle.isEmpty())
                mCurrentTitle = stripExtension(path);
            if (mCurrentArtist == null) mCurrentArtist = "";
            if (mCurrentAlbum  == null) mCurrentAlbum  = "";
            Log.d(TAG, "Direct tags: " + mCurrentTitle + " / " + mCurrentArtist);
        } catch (Exception e) {
            Log.e(TAG, "MediaMetadataRetriever error: " + e);
            mCurrentTitle    = stripExtension(path);
            mCurrentArtist   = "";
            mCurrentAlbum    = "";
            mCurrentAlbumArt = null;
            mCurrentAudioId  = -1;
            mCurrentAlbumId  = -1;
            mCurrentArtistId = -1;
        } finally {
            try { r.release(); } catch (Exception ignored) {}
            if (fis != null) try { fis.close(); } catch (Exception ignored) {}
        }
    }

    private Bitmap loadAlbumArt(long albumId) {
        if (albumId <= 0) return null;
        try {
            Uri uri = ContentUris.withAppendedId(
                    Uri.parse("content://media/external/audio/albumart"), albumId);
            BitmapFactory.Options opts = new BitmapFactory.Options();
            opts.inJustDecodeBounds = true;
            InputStream is = getContentResolver().openInputStream(uri);
            if (is == null) return null;
            BitmapFactory.decodeStream(is, null, opts);
            is.close();
            opts.inSampleSize = sampleSize(opts.outWidth, opts.outHeight, MAX_ART_PX);
            opts.inJustDecodeBounds = false;
            is = getContentResolver().openInputStream(uri);
            if (is == null) return null;
            Bitmap bmp = BitmapFactory.decodeStream(is, null, opts);
            is.close();
            Log.v(TAG, "loadAlbumArt id=" + albumId + " → "
                    + (bmp != null ? bmp.getWidth() + "x" + bmp.getHeight() : "null"));
            return bmp;
        } catch (Exception e) {
            Log.v(TAG, "loadAlbumArt id=" + albumId + " failed: " + e.getMessage());
            return null;
        }
    }

    private Bitmap decodeSampled(byte[] data, int maxPx) {
        BitmapFactory.Options opts = new BitmapFactory.Options();
        opts.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(data, 0, data.length, opts);
        opts.inSampleSize = sampleSize(opts.outWidth, opts.outHeight, maxPx);
        opts.inJustDecodeBounds = false;
        return BitmapFactory.decodeByteArray(data, 0, data.length, opts);
    }

    private int sampleSize(int w, int h, int maxPx) {
        int s = 1;
        while ((h / (s * 2)) >= maxPx && (w / (s * 2)) >= maxPx) s *= 2;
        return s;
    }

    private String stripExtension(String path) {
        if (path == null) return "";
        int slash = path.lastIndexOf('/');
        String name = slash >= 0 ? path.substring(slash + 1) : path;
        int dot = name.lastIndexOf('.');
        return dot > 0 ? name.substring(0, dot) : name;
    }

    private static String safeString(String s) { return s == null ? "" : s; }
}
