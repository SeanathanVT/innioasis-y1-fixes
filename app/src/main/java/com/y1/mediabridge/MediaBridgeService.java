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
import android.os.IInterface;
import android.os.Looper;
import android.os.Parcel;
import android.os.RemoteException;
import android.os.SystemClock;
import android.provider.MediaStore;
import android.util.Log;
import android.view.KeyEvent;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
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

    /** Callback IBinders registered by MtkBt via IBTAvrcpMusic.registerCallback. */
    private final CopyOnWriteArrayList<IBinder> mAvrcpCallbacks =
            new CopyOnWriteArrayList<IBinder>();

    // =======================================================================
    // The binder — single instance, dual-interface dispatch
    // =======================================================================

    private final Binder mBinder = new Binder() {
        @Override
        protected boolean onTransact(int code, Parcel data, Parcel reply, int flags)
                throws RemoteException {
            // INTERFACE_TRANSACTION has no strictmode+token prefix; let
            // Binder.onTransact return our attached descriptor directly.
            if (code == INTERFACE_TRANSACTION) {
                return super.onTransact(code, data, reply, flags);
            }

            // Peek the descriptor at the head of the parcel. writeInterfaceToken
            // writes: int32 strictModePolicy, then UTF-16 descriptor string.
            // We read both to advance past them, then rewind so each case can
            // call enforceInterface itself — that keeps the dispatch symmetric
            // with AOSP-generated stub code.
            final int startPos = data.dataPosition();
            String descriptor;
            try {
                data.readInt();                  // strictModePolicy (discarded)
                descriptor = data.readString();  // interface token
            } catch (Exception e) {
                data.setDataPosition(startPos);
                return super.onTransact(code, data, reply, flags);
            }
            data.setDataPosition(startPos);

            Log.v(TAG, "onTransact code=" + code + " descriptor=" + descriptor);

            if (DESCRIPTOR_AVRCP_MUSIC.equals(descriptor)) {
                return handleAvrcpMusic(code, data, reply);
            }
            if (DESCRIPTOR_MEDIA_PLAYBACK.equals(descriptor)) {
                return handleMediaPlayback(code, data, reply);
            }
            Log.v(TAG, "onTransact unhandled descriptor, super-fallthrough");
            return super.onTransact(code, data, reply, flags);
        }
    };

    // -----------------------------------------------------------------------
    // IBTAvrcpMusic dispatch — every declared code returns a well-formed reply
    // even when we have no semantic answer, because returning false / garbage
    // makes MtkBt's BTAvrcpMusicAdapter abort registration (see class doc).
    // -----------------------------------------------------------------------

    private boolean handleAvrcpMusic(int code, Parcel data, Parcel reply)
            throws RemoteException {
        switch (code) {
            case 1: { // registerCallback(IBTAvrcpMusicCallback cb)
                //
                // CRITICAL: enforceInterface MUST run before readStrongBinder.
                // It reads and discards the strictModePolicy int32 AND the
                // UTF-16 interface token string, advancing the parcel cursor
                // past them. Without it, readStrongBinder() reads the token
                // bytes as flat_binder_object data and returns null — the
                // callback is silently dropped, MtkBt never hears from us,
                // and the car sees no metadata.
                //
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                IBinder cb = data.readStrongBinder();
                if (cb != null && !mAvrcpCallbacks.contains(cb)) {
                    mAvrcpCallbacks.add(cb);
                    Log.d(TAG, "IBTAvrcpMusic.registerCallback total="
                            + mAvrcpCallbacks.size());
                    // Push current state so MtkBt's internal callback arrays
                    // populate right now, not on the next track change.
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
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                IBinder cb = data.readStrongBinder();
                if (cb != null) mAvrcpCallbacks.remove(cb);
                Log.d(TAG, "IBTAvrcpMusic.unregisterCallback total="
                        + mAvrcpCallbacks.size());
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
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                byte eventId = data.readByte();
                int  param   = data.readInt();
                Log.d(TAG, "IBTAvrcpMusic.regNotificationEvent event=0x"
                        + Integer.toHexString(eventId & 0xff) + " param=" + param);
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;
            }

            case 4: { // setPlayerApplicationSettingValue(byte attr, byte val) -> boolean
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                // Read args so the parcel is consumed; we don't apply them.
                try { data.readByte(); data.readByte(); } catch (Exception ignored) {}
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;
            }

            case 5: { // getCapabilities() -> byte[]
                // MtkBt stashes this in mCapabilities. A nonempty byte array
                // avoids NPE paths in BTAvrcpMusicAdapter. We return an empty
                // array — length 0 — which Parcel encodes as writeInt(0).
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeByteArray(new byte[0]);
                }
                return true;
            }

            // Transport commands — forward as media keys to the stock player.
            // Note: IBTAvrcpMusic code 6 = play, NOT pause (differs from
            // IMediaPlaybackService which uses 6=pause, 7=play).
            case 6:  return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PLAY);
            case 7:  return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_STOP);
            case 8:  return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PAUSE);
            case 9:  return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PLAY);
            case 10: return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PREVIOUS);
            case 11: return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_NEXT);
            case 12: return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_PREVIOUS); // prevGroup
            case 13: return avrcpAck(data, reply, KeyEvent.KEYCODE_MEDIA_NEXT);     // nextGroup

            // Setter/getter pairs for player-app settings. Setters return
            // boolean success; getters return int (zero = not-applicable).
            case 14: // setEqualizeMode(int) -> boolean
            case 16: // setShuffleMode(int)  -> boolean
            case 18: // setRepeatMode(int)   -> boolean
            case 20: // setScanMode(int)     -> boolean
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                try { data.readInt(); } catch (Exception ignored) {}
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;
            case 15: // getEqualizeMode()  -> int
            case 17: // getShuffleMode()   -> int
            case 19: // getRepeatMode()    -> int
            case 21: // getScanMode()      -> int
            case 36: // getQueuePosition() -> int
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                if (reply != null) { reply.writeNoException(); reply.writeInt(0); }
                return true;

            case 22: // informDisplayableCharacterSet(int) -> boolean
            case 23: // informBatteryStatusOfCT()         -> boolean
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                if (code == 22) { try { data.readInt(); } catch (Exception ignored) {} }
                if (reply != null) { reply.writeNoException(); reply.writeInt(1); }
                return true;

            case 24: // getPlayStatus() -> byte (1=stopped, 2=playing, 3=paused)
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeByte(mIsPlaying ? (byte) 2 : (byte) 3);
                }
                return true;
            case 25: // position() -> long
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                if (reply != null) { reply.writeNoException(); reply.writeLong(computePosition()); }
                return true;
            case 26: // duration() -> long
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentDuration); }
                return true;
            case 27: // getAudioId() -> long
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentAudioId); }
                return true;
            case 28: // getTrackName() -> String
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                if (reply != null) { reply.writeNoException(); reply.writeString(safeString(mCurrentTitle)); }
                return true;
            case 29: // getAlbumName() -> String
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                if (reply != null) { reply.writeNoException(); reply.writeString(safeString(mCurrentAlbum)); }
                return true;
            case 30: // getAlbumId() -> long (IBTAvrcpMusic numbering)
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentAlbumId); }
                return true;
            case 31: // getArtistName() -> String
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                if (reply != null) { reply.writeNoException(); reply.writeString(safeString(mCurrentArtist)); }
                return true;

            case 32: // enqueue(long[], int) -> void
            case 35: // open(long[], int)    -> void
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                try { data.createLongArray(); data.readInt(); } catch (Exception ignored) {}
                if (reply != null) reply.writeNoException();
                return true;

            case 33: // getNowPlaying() -> long[]
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                if (reply != null) {
                    reply.writeNoException();
                    reply.writeLongArray(new long[0]);
                }
                return true;

            case 34: // getNowPlayingItemName(long) -> String
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
                try { data.readLong(); } catch (Exception ignored) {}
                if (reply != null) { reply.writeNoException(); reply.writeString(""); }
                return true;

            case 37: // setQueuePosition(int) -> void
                data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
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
        data.enforceInterface(DESCRIPTOR_AVRCP_MUSIC);
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
    // The brief's code table had a few codes off — corrected here from the
    // authoritative TRANSACTION_* fields extracted from the DEX.
    // -----------------------------------------------------------------------

    private boolean handleMediaPlayback(int code, Parcel data, Parcel reply)
            throws RemoteException {
        data.enforceInterface(DESCRIPTOR_MEDIA_PLAYBACK);
        switch (code) {
            case 4:  // isPlaying() -> int (0/1)
                if (reply != null) { reply.writeNoException(); reply.writeInt(mIsPlaying ? 1 : 0); }
                return true;
            case 10: // duration() -> long
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentDuration); }
                return true;
            case 11: // position() -> long
                if (reply != null) { reply.writeNoException(); reply.writeLong(computePosition()); }
                return true;
            case 13: // getTrackName() -> String
                if (reply != null) { reply.writeNoException(); reply.writeString(safeString(mCurrentTitle)); }
                return true;
            case 14: // getAlbumName() -> String
                if (reply != null) { reply.writeNoException(); reply.writeString(safeString(mCurrentAlbum)); }
                return true;
            case 15: // getAlbumId() -> long
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentAlbumId); }
                return true;
            case 16: // getArtistName() -> String
                if (reply != null) { reply.writeNoException(); reply.writeString(safeString(mCurrentArtist)); }
                return true;
            case 17: // getArtistId() -> long
                if (reply != null) { reply.writeNoException(); reply.writeLong(mCurrentArtistId); }
                return true;
            case 24: // getAudioId() -> long
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
                cb.transact(code, data, reply, 0);
            } catch (RemoteException e) {
                Log.w(TAG, "AVRCP callback transact failed code=" + code
                        + " — dropping: " + e);
                mAvrcpCallbacks.remove(cb);
            } finally {
                reply.recycle();
                data.recycle();
            }
        }
    }

    private void notifyPlaybackStatus(byte status) { notifyAvrcpCallbacks(1, status); }
    private void notifyTrackChanged(long id)       { notifyAvrcpCallbacks(2, id); }

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
        Log.d(TAG, "MediaBridgeService created (pid=" + android.os.Process.myPid()
                + " uid=" + android.os.Process.myUid() + ")");

        // Attach a non-null IInterface under the IBTAvrcpMusic descriptor.
        // This only matters for in-process queryLocalInterface() lookups;
        // MtkBt is out-of-process so queryLocalInterface always returns null
        // there regardless. Harmless belt-and-suspenders.
        mBinder.attachInterface(new IInterface() {
            @Override public IBinder asBinder() { return mBinder; }
        }, DESCRIPTOR_AVRCP_MUSIC);

        mAudioManager = (AudioManager) getSystemService(AUDIO_SERVICE);
        setupRemoteControlClient();
        startLogcatMonitor();
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
        Log.d(TAG, "State change: " + (playing ? "playing" : "paused"));
        // Freeze position estimate before flipping the clock.
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
            // Not indexed yet — scan this single file, then retry.
            MediaScannerConnection.scanFile(this, new String[]{ path }, null,
                    new MediaScannerConnection.OnScanCompletedListener() {
                        @Override
                        public void onScanCompleted(String p, Uri uri) {
                            final String finalPath = p;
                            mMainHandler.post(new Runnable() {
                                @Override public void run() {
                                    if (!queryMetadataFromStore(finalPath)) {
                                        readTagsDirectly(finalPath);
                                    }
                                    broadcastTrackAndState();
                                }
                            });
                        }
                    });
            return;
        }
        broadcastTrackAndState();
    }

    private void broadcastTrackAndState() {
        publishMetadata();
        publishState();
        sendMusicBroadcast("com.android.music.metachanged");
        notifyTrackChanged(mCurrentAudioId);
        notifyPlaybackStatus(mIsPlaying ? (byte) 2 : (byte) 3);
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
                mCurrentTitle    = cursor.getString(cursor.getColumnIndex(MediaStore.Audio.Media.TITLE));
                mCurrentArtist   = cursor.getString(cursor.getColumnIndex(MediaStore.Audio.Media.ARTIST));
                mCurrentAlbum    = cursor.getString(cursor.getColumnIndex(MediaStore.Audio.Media.ALBUM));
                mCurrentDuration = cursor.getLong(cursor.getColumnIndex(MediaStore.Audio.Media.DURATION));
                mCurrentAlbumId  = cursor.getLong(cursor.getColumnIndex(MediaStore.Audio.Media.ALBUM_ID));
                mCurrentArtistId = cursor.getLong(cursor.getColumnIndex(MediaStore.Audio.Media.ARTIST_ID));
                mCurrentAudioId  = cursor.getLong(cursor.getColumnIndex(MediaStore.Audio.Media._ID));
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
        try {
            r.setDataSource(path);
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
            Log.e(TAG, "MediaMetadataRetriever error: " + e.getMessage());
            mCurrentTitle    = stripExtension(path);
            mCurrentArtist   = "";
            mCurrentAlbum    = "";
            mCurrentAlbumArt = null;
            mCurrentAudioId  = -1;
            mCurrentAlbumId  = -1;
            mCurrentArtistId = -1;
        } finally {
            try { r.release(); } catch (Exception ignored) {}
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
            return bmp;
        } catch (Exception e) {
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
